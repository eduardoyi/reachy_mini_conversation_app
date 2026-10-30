// Command reachy-calls is the stage 1 check for WhatsApp calls through Reachy
// (docs/plans/whatsapp-calls.md): a second linked device on the spare number that
// places and answers 1:1 calls with approved people only.
//
//	reachy-calls pair <spare-number>                 Link to the spare number with a pairing code.
//	reachy-calls call <name> <play.wav> <record.wav> [max-s]
//	                                                 Call an approved person, play a file, record them.
//	reachy-calls wait <play.wav> <record.wav> [max-s]
//	                                                 Answer the next call from an approved person.
//
// The session lives in REACHY_CALLS_DIR (default ~/.local/share/reachy-whatsapp-calls) and the
// approved list in REACHY_CALLS_APPROVED (default ~/.config/reachy-whatsapp/calls.json):
//
//	{"approved": [{"name": "Eduardo", "phone": "+15551234567"}]}
//
// Numbers never reach the log: people are named as the approved list names them.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	meowcaller "github.com/purpshell/meowcaller"
	"github.com/purpshell/meowcaller/diag"
	"github.com/rs/zerolog"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCompanionReg"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"

	_ "modernc.org/sqlite"
)

const defaultMaxSeconds = 60

var notDigits = regexp.MustCompile(`\D`)

// diagRecorder is set when REACHY_CALLS_DIAG asks for packet diagnostics; closed when the program ends.
var diagRecorder *diag.Recorder

// person is one approved entry: the name it is logged as and its phone digits.
type person struct {
	Name   string `json:"name"`
	Phone  string `json:"phone"`
	digits string
}

func main() {
	log := zerolog.New(zerolog.ConsoleWriter{Out: os.Stderr, TimeFormat: "15:04:05.000"}).
		Level(zerolog.InfoLevel).With().Timestamp().Logger()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx = log.WithContext(ctx)

	args := os.Args[1:]
	var err error
	switch {
	case len(args) == 2 && args[0] == "pair":
		err = pair(ctx, args[1])
	case (len(args) == 4 || len(args) == 5) && args[0] == "call":
		err = call(ctx, args[1], args[2], args[3], maxSeconds(args, 4))
	case (len(args) == 3 || len(args) == 4) && args[0] == "wait":
		err = wait(ctx, args[1], args[2], maxSeconds(args, 3))
	default:
		fmt.Fprintln(os.Stderr, "usage: reachy-calls pair <spare-number> | call <name> <play.wav> <record.wav> [max-s] | wait <play.wav> <record.wav> [max-s]")
		os.Exit(2)
	}
	if diagRecorder != nil {
		diagRecorder.Close()
	}
	if err != nil {
		log.Error().Err(err).Msg("failed")
		os.Exit(1)
	}
}

func maxSeconds(args []string, index int) time.Duration {
	if len(args) > index {
		if seconds, err := strconv.Atoi(args[index]); err == nil && seconds > 0 {
			return time.Duration(seconds) * time.Second
		}
	}
	return defaultMaxSeconds * time.Second
}

func homePath(env, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(env)); value != "" {
		return value
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, fallback)
}

func loadApproved() ([]person, error) {
	path := homePath("REACHY_CALLS_APPROVED", ".config/reachy-whatsapp/calls.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read approved list: %w", err)
	}
	var file struct {
		Approved []person `json:"approved"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		return nil, fmt.Errorf("parse approved list: %w", err)
	}
	var people []person
	for _, p := range file.Approved {
		p.Name = strings.TrimSpace(p.Name)
		p.digits = notDigits.ReplaceAllString(p.Phone, "")
		if p.Name != "" && len(p.digits) > 6 {
			people = append(people, p)
		}
	}
	if len(people) == 0 {
		return nil, errors.New("nobody is approved")
	}
	return people, nil
}

// openClient opens the session store; the meowcaller client must be made before Connect.
func openClient(ctx context.Context) (*whatsmeow.Client, error) {
	dir := homePath("REACHY_CALLS_DIR", ".local/share/reachy-whatsapp-calls")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	store.DeviceProps.Os = proto.String("Mac OS")
	store.DeviceProps.PlatformType = waCompanionReg.DeviceProps_CHROME.Enum()
	waLogger := zerolog.Ctx(ctx).Level(zerolog.WarnLevel)
	dsn := "file:" + filepath.Join(dir, "calls.db") + "?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)"
	container, err := sqlstore.New(ctx, "sqlite", dsn, waLog.Zerolog(waLogger).Sub("db"))
	if err != nil {
		return nil, fmt.Errorf("open store: %w", err)
	}
	device, err := container.GetFirstDevice(ctx)
	if err != nil {
		return nil, fmt.Errorf("load device: %w", err)
	}
	return whatsmeow.NewClient(device, waLog.Zerolog(waLogger).Sub("wa")), nil
}

func pair(ctx context.Context, spareNumber string) error {
	log := zerolog.Ctx(ctx)
	wa, err := openClient(ctx)
	if err != nil {
		return err
	}
	if wa.Store.ID != nil {
		return errors.New("already linked; delete the session store to link again")
	}
	qr, err := wa.GetQRChannel(ctx)
	if err != nil {
		return err
	}
	if err := wa.Connect(); err != nil {
		return fmt.Errorf("connect: %w", err)
	}
	defer wa.Disconnect()
	asked := false
	for evt := range qr {
		switch {
		case evt.Event == "code" && !asked:
			asked = true
			code, err := wa.PairPhone(ctx, spareNumber, true, whatsmeow.PairClientChrome, "Chrome (Mac OS)")
			if err != nil {
				return fmt.Errorf("pairing code: %w", err)
			}
			fmt.Printf("\nOn the spare phone: WhatsApp > Linked devices > Link a device > Link with phone number instead\nCode: %s\n\n", code)
		case evt.Event == "success":
			log.Info().Msg("linked")
		case evt.Event != "code":
			log.Info().Str("event", evt.Event).Msg("pairing")
		}
	}
	return waitReady(ctx, wa)
}

// connect logs the linked device in and announces it, so call signaling reaches it.
func connect(ctx context.Context) (*whatsmeow.Client, *meowcaller.Client, error) {
	wa, err := openClient(ctx)
	if err != nil {
		return nil, nil, err
	}
	if wa.Store.ID == nil {
		return nil, nil, errors.New("not linked yet; run pair first")
	}
	// REACHY_CALLS_DEBUG=1 logs the call engine in full (it can include numbers: for checks by hand only).
	callLevel := zerolog.WarnLevel
	if os.Getenv("REACHY_CALLS_DEBUG") == "1" {
		callLevel = zerolog.DebugLevel
	}
	options := []meowcaller.Option{meowcaller.WithLogger(zerolog.Ctx(ctx).Level(callLevel))}
	// REACHY_CALLS_DIAG=<dir> records every packet, including key material: local checks only, delete after.
	if dir := strings.TrimSpace(os.Getenv("REACHY_CALLS_DIAG")); dir != "" {
		recorder, err := diag.NewRecorder(dir)
		if err != nil {
			return nil, nil, fmt.Errorf("diagnostics: %w", err)
		}
		options = append(options, meowcaller.WithDiagnostics(recorder))
		go func() { <-ctx.Done(); recorder.Close() }()
		diagRecorder = recorder
	}
	client := meowcaller.NewClient(wa, options...)
	if err := wa.Connect(); err != nil {
		return nil, nil, fmt.Errorf("connect: %w", err)
	}
	if err := waitReady(ctx, wa); err != nil {
		wa.Disconnect()
		return nil, nil, err
	}
	if wa.Store.PushName == "" {
		wa.Store.PushName = "Reachy"
	}
	if err := wa.SendPresence(ctx, types.PresenceAvailable); err != nil {
		zerolog.Ctx(ctx).Warn().Err(err).Msg("presence not sent")
	}
	return wa, client, nil
}

// waitReady waits for the logged-in connection, through the reconnect that follows pairing.
func waitReady(ctx context.Context, wa *whatsmeow.Client) error {
	ready := make(chan struct{}, 8)
	id := wa.AddEventHandler(func(evt any) {
		if _, ok := evt.(*events.Connected); ok {
			select {
			case ready <- struct{}{}:
			default:
			}
		}
	})
	defer wa.RemoveEventHandler(id)
	deadline := time.After(60 * time.Second)
	for !(wa.IsConnected() && wa.IsLoggedIn()) {
		select {
		case <-ready:
		case <-deadline:
			return errors.New("timed out connecting")
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return nil
}

func call(ctx context.Context, name, playPath, recordPath string, limit time.Duration) error {
	approved, err := loadApproved()
	if err != nil {
		return err
	}
	var target *person
	for i := range approved {
		if strings.EqualFold(approved[i].Name, name) {
			target = &approved[i]
		}
	}
	if target == nil {
		return fmt.Errorf("%q is not approved", name)
	}
	wa, client, err := connect(ctx)
	if err != nil {
		return err
	}
	defer wa.Disconnect()
	client.OnIncomingCall(func(c *meowcaller.Call) { _ = c.Reject() }) // stage 1: no answering here
	c, err := client.Call(ctx, "+"+target.digits)
	if err != nil {
		return fmt.Errorf("place call: %w", err)
	}
	zerolog.Ctx(ctx).Info().Str("to", target.Name).Msg("ringing")
	return runCall(ctx, c, target.Name, playPath, recordPath, limit)
}

func wait(ctx context.Context, playPath, recordPath string, limit time.Duration) error {
	approved, err := loadApproved()
	if err != nil {
		return err
	}
	wa, client, err := connect(ctx)
	if err != nil {
		return err
	}
	defer wa.Disconnect()
	log := zerolog.Ctx(ctx)
	answered := make(chan *meowcaller.Call, 1)
	var who sync.Map
	client.OnIncomingCall(func(c *meowcaller.Call) {
		name := approvedName(ctx, wa, approved, c.Peer())
		if name == "" {
			log.Info().Msg("call from someone not approved; declined")
			_ = c.Reject()
			return
		}
		who.Store(c.ID(), name)
		select {
		case answered <- c:
		default:
			_ = c.Reject() // one call at a time
		}
	})
	log.Info().Msg("waiting for a call from an approved person")
	select {
	case c := <-answered:
		if err := c.Answer(); err != nil {
			return fmt.Errorf("answer: %w", err)
		}
		name, _ := who.Load(c.ID())
		log.Info().Str("from", name.(string)).Msg("answered")
		return runCall(ctx, c, name.(string), playPath, recordPath, limit)
	case <-ctx.Done():
		return nil
	}
}

// approvedName returns the approved name for a caller (a phone or LID JID), or "".
func approvedName(ctx context.Context, wa *whatsmeow.Client, approved []person, peer types.JID) string {
	if peer.Server == types.HiddenUserServer {
		if pn, err := wa.Store.LIDs.GetPNForLID(ctx, peer); err == nil && !pn.IsEmpty() {
			peer = pn
		} else {
			return ""
		}
	}
	for _, p := range approved {
		if peer.User == p.digits {
			return p.Name
		}
	}
	return ""
}

// levelSink records the peer and keeps counts for the end-of-call summary.
type levelSink struct {
	inner  meowcaller.AudioSink
	frames int
	loud   int
	peak   float64
}

func (s *levelSink) WriteFrame(frame []float32) error {
	var sum float64
	for _, v := range frame {
		sum += float64(v) * float64(v)
	}
	rms := math.Sqrt(sum / float64(max(1, len(frame))))
	s.frames++
	if rms > 0.01 {
		s.loud++
	}
	s.peak = math.Max(s.peak, rms)
	return s.inner.WriteFrame(frame)
}

func (s *levelSink) Close() error { return s.inner.Close() }

func runCall(ctx context.Context, c *meowcaller.Call, name, playPath, recordPath string, limit time.Duration) error {
	log := zerolog.Ctx(ctx)
	recorder, err := meowcaller.WAVRecorder(recordPath)
	if err != nil {
		return fmt.Errorf("open recorder: %w", err)
	}
	sink := &levelSink{inner: recorder}
	c.Receive(sink)
	ended := make(chan string, 1)
	var connectedAt time.Time
	unanswered := time.AfterFunc(60*time.Second, func() { _ = c.Hangup() })
	c.OnReady(func() {
		unanswered.Stop()
		connectedAt = time.Now()
		time.AfterFunc(limit, func() { _ = c.Hangup() })
		log.Info().Str("with", name).Msg("connected; playing the test sound")
		src, err := meowcaller.WAVFile(playPath)
		if err != nil {
			log.Error().Err(err).Msg("open test sound")
			return
		}
		c.Play(src)
	})
	c.OnEnd(func(reason string) {
		select {
		case ended <- reason:
		default:
		}
	})
	reason := ""
	select {
	case reason = <-ended:
	case <-time.After(limit + 70*time.Second): // the end never reported
		reason = "no end reported"
		_ = c.Hangup()
	case <-ctx.Done():
		reason = "stopped"
		_ = c.Hangup()
	}
	_ = sink.Close()
	// Give a hang-up time to reach WhatsApp before the connection closes, or the other phone only sees
	// the media stop and shows "Reconnecting".
	time.Sleep(2 * time.Second)
	talked := 0.0
	if !connectedAt.IsZero() {
		talked = time.Since(connectedAt).Seconds()
	}
	frameS := float64(meowcaller.FrameSamples) / float64(meowcaller.SampleRate)
	log.Info().
		Str("with", name).
		Str("reason", reason).
		Float64("connected_s", math.Round(talked*10)/10).
		Float64("heard_s", math.Round(float64(sink.frames)*frameS*10)/10).
		Float64("voice_s", math.Round(float64(sink.loud)*frameS*10)/10).
		Float64("peak_rms", math.Round(sink.peak*1000)/1000).
		Msg("call ended")
	return nil
}
