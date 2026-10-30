package main

import (
	"os"
	"path/filepath"
	"testing"

	meowcaller "github.com/purpshell/meowcaller"
)

func TestLoadApprovedKeepsOnlyNamedNumbers(t *testing.T) {
	path := filepath.Join(t.TempDir(), "calls.json")
	data := `{"approved": [
		{"name": "Test Person", "phone": "+1 555 010 0000"},
		{"name": "", "phone": "+15550100001"},
		{"name": "Too Short", "phone": "123"}
	]}`
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REACHY_CALLS_APPROVED", path)
	people, err := loadApproved()
	if err != nil {
		t.Fatal(err)
	}
	if len(people) != 1 || people[0].Name != "Test Person" || people[0].digits != "15550100000" {
		t.Fatalf("approved = %+v", people)
	}
}

func TestLoadApprovedRefusesAnEmptyList(t *testing.T) {
	path := filepath.Join(t.TempDir(), "calls.json")
	if err := os.WriteFile(path, []byte(`{"approved": []}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REACHY_CALLS_APPROVED", path)
	if _, err := loadApproved(); err == nil {
		t.Fatal("an empty list must be refused")
	}
}

func TestPCMSourceSendsSilenceUntilAFrameHasArrived(t *testing.T) {
	source := &pcmSource{}
	frame, _ := source.ReadFrame()
	if len(frame) != meowcaller.FrameSamples || frame[0] != 0 {
		t.Fatal("expected a silent frame")
	}
	half := make([]float32, meowcaller.FrameSamples)
	for i := range half {
		half[i] = 0.5
	}
	source.push(pcm16(half))
	frame, _ = source.ReadFrame()
	if frame[0] < 0.49 || frame[0] > 0.51 {
		t.Fatalf("expected the pushed audio, got %v", frame[0])
	}
}

func TestPCMSourceDropsTheOldestWhenItFallsBehind(t *testing.T) {
	source := &pcmSource{}
	source.push(make([]byte, 2*(maxBufferedSamples+meowcaller.FrameSamples)))
	if len(source.buf) != maxBufferedSamples {
		t.Fatalf("buffered %d samples", len(source.buf))
	}
}
