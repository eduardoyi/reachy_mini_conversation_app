package main

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	meowcaller "github.com/purpshell/meowcaller"
	"github.com/rs/zerolog"
	"go.mau.fi/whatsmeow"
)

const (
	defaultServeAddr = "127.0.0.1:8793"
	// A call that is not answered in this long is hung up; one that lasts this long is ended.
	unansweredLimit = 60 * time.Second
	callLimit       = 60 * time.Minute
	// Mic audio waiting to go out is capped at this many samples (0.3 s); older audio is dropped.
	maxBufferedSamples = meowcaller.SampleRate * 3 / 10
)

// service keeps the linked device online and runs one call at a time for the app, which talks to it
// over a WebSocket on the loopback interface:
//
//	app -> service, text:   {"op": "call", "name": "Eduardo"}, then {"op": "hangup"}
//	service -> app, text:   {"event": "ringing" | "connected" | "ended", "reason": "...", "error": "..."}
//	both ways, binary:      16 kHz mono s16le PCM (the mic out, the other person in)
//
// Closing the socket hangs up. Only people on the approved list can be called.
type service struct {
	wa     *whatsmeow.Client
	client *meowcaller.Client
	token  []byte
	mu     sync.Mutex
	busy   bool
}

func serve(ctx context.Context, addr string) error {
	log := zerolog.Ctx(ctx)
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return err
	}
	if ip := net.ParseIP(host); host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		return fmt.Errorf("refusing to listen beyond this machine: %s", addr)
	}
	token, err := loadToken()
	if err != nil {
		return err
	}
	wa, client, err := connect(ctx)
	if err != nil {
		return err
	}
	defer wa.Disconnect()
	svc := &service{wa: wa, client: client, token: token}
	client.OnIncomingCall(func(c *meowcaller.Call) {
		// Stage 3 announces and answers approved callers; until then every incoming call is declined.
		name := "someone not approved"
		if approved, err := loadApproved(); err == nil {
			if found := approvedName(ctx, wa, approved, c.Peer()); found != "" {
				name = found
			}
		}
		log.Info().Str("from", name).Msg("incoming call declined (not answered yet)")
		_ = c.Reject()
	})

	mux := http.NewServeMux()
	mux.HandleFunc("/call", func(w http.ResponseWriter, r *http.Request) { svc.handleCall(ctx, w, r) })
	server := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	log.Info().Str("addr", addr).Msg("call service ready")
	if err := server.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

// loadToken returns the shared secret the app sends, creating it (mode 600) on first use.
func loadToken() ([]byte, error) {
	dir := homePath("REACHY_CALLS_DIR", ".local/share/reachy-whatsapp-calls")
	path := filepath.Join(dir, "token")
	if raw, err := os.ReadFile(path); err == nil {
		if token := strings.TrimSpace(string(raw)); token != "" {
			return []byte(token), nil
		}
	}
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return nil, err
	}
	token := hex.EncodeToString(secret)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	if err := os.WriteFile(path, []byte(token+"\n"), 0o600); err != nil {
		return nil, err
	}
	return []byte(token), nil
}

func (s *service) claim() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.busy {
		return false
	}
	s.busy = true
	return true
}

func (s *service) release() {
	s.mu.Lock()
	s.busy = false
	s.mu.Unlock()
}

func (s *service) handleCall(ctx context.Context, w http.ResponseWriter, r *http.Request) {
	log := zerolog.Ctx(ctx)
	given := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if subtle.ConstantTimeCompare([]byte(given), s.token) != 1 {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	if !s.claim() {
		http.Error(w, "a call is already on", http.StatusConflict)
		return
	}
	defer s.release()
	conn, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	defer conn.CloseNow()
	conn.SetReadLimit(1 << 20)
	send := func(event map[string]string) {
		payload, _ := json.Marshal(event)
		write, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		_ = conn.Write(write, websocket.MessageText, payload)
	}

	kind, first, err := conn.Read(ctx)
	var request struct {
		Op   string `json:"op"`
		Name string `json:"name"`
	}
	if err != nil || kind != websocket.MessageText || json.Unmarshal(first, &request) != nil || request.Op != "call" {
		send(map[string]string{"event": "ended", "reason": "failed", "error": "expected a call request"})
		return
	}
	approved, err := loadApproved()
	if err != nil {
		send(map[string]string{"event": "ended", "reason": "failed", "error": "no approved list"})
		return
	}
	var target *person
	for i := range approved {
		if strings.EqualFold(approved[i].Name, strings.TrimSpace(request.Name)) {
			target = &approved[i]
		}
	}
	if target == nil {
		log.Info().Msg("call refused: not approved")
		send(map[string]string{"event": "ended", "reason": "not_approved"})
		return
	}
	call, err := s.client.Call(ctx, "+"+target.digits)
	if err != nil {
		log.Warn().Err(err).Str("to", target.Name).Msg("call not placed")
		send(map[string]string{"event": "ended", "reason": "failed", "error": "call not placed"})
		return
	}
	log.Info().Str("to", target.Name).Msg("ringing")
	send(map[string]string{"event": "ringing"})

	mic := &pcmSource{}
	ended := make(chan string, 1)
	var mu sync.Mutex
	connected, hungUp := false, false
	hangUp := func() {
		mu.Lock()
		hungUp = true
		mu.Unlock()
		_ = call.Hangup()
	}
	unanswered := time.AfterFunc(unansweredLimit, hangUp)
	defer unanswered.Stop()
	var limit *time.Timer
	call.Receive(meowcaller.SinkFunc(func(frame []float32) {
		write, cancel := context.WithTimeout(ctx, time.Second)
		defer cancel()
		_ = conn.Write(write, websocket.MessageBinary, pcm16(frame))
	}))
	call.OnReady(func() {
		unanswered.Stop()
		mu.Lock()
		connected = true
		limit = time.AfterFunc(callLimit, hangUp)
		mu.Unlock()
		call.Play(mic)
		log.Info().Str("with", target.Name).Msg("connected")
		send(map[string]string{"event": "connected"})
	})
	call.OnEnd(func(reason string) {
		select {
		case ended <- reason:
		default:
		}
	})

	// The app's audio and requests; a closed socket hangs up.
	go func() {
		for {
			kind, data, err := conn.Read(ctx)
			if err != nil {
				hangUp()
				return
			}
			if kind == websocket.MessageBinary {
				mic.push(data)
				continue
			}
			var op struct {
				Op string `json:"op"`
			}
			if json.Unmarshal(data, &op) == nil && op.Op == "hangup" {
				hangUp()
			}
		}
	}()

	select {
	case <-ended:
	case <-ctx.Done():
		_ = call.Hangup()
	}
	mu.Lock()
	reason := "ended"
	switch {
	case !connected && hungUp:
		reason = "no_answer"
	case !connected:
		reason = "declined"
	case hungUp:
		reason = "hung_up"
	}
	if limit != nil {
		limit.Stop()
	}
	mu.Unlock()
	log.Info().Str("with", target.Name).Str("reason", reason).Msg("call ended")
	send(map[string]string{"event": "ended", "reason": reason})
	_ = conn.Close(websocket.StatusNormalClosure, "")
}

// pcmSource plays the app's mic audio into the call, or silence while none has arrived.
type pcmSource struct {
	mu  sync.Mutex
	buf []float32
}

func (s *pcmSource) push(data []byte) {
	samples := make([]float32, len(data)/2)
	for i := range samples {
		samples[i] = float32(int16(binary.LittleEndian.Uint16(data[2*i:]))) / 32768
	}
	s.mu.Lock()
	s.buf = append(s.buf, samples...)
	if extra := len(s.buf) - maxBufferedSamples; extra > 0 {
		s.buf = s.buf[extra:] // the network fell behind: drop the oldest
	}
	s.mu.Unlock()
}

func (s *pcmSource) ReadFrame() ([]float32, error) {
	frame := make([]float32, meowcaller.FrameSamples)
	s.mu.Lock()
	if len(s.buf) >= len(frame) {
		copy(frame, s.buf)
		s.buf = s.buf[len(frame):]
	}
	s.mu.Unlock()
	return frame, nil
}

func (s *pcmSource) Close() error { return nil }

func pcm16(frame []float32) []byte {
	out := make([]byte, 2*len(frame))
	for i, v := range frame {
		clipped := math.Max(-1, math.Min(1, float64(v)))
		binary.LittleEndian.PutUint16(out[2*i:], uint16(int16(clipped*32767)))
	}
	return out
}
