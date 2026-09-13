package display

import (
	"fmt"
	"time"
)

// Sink renders frames. The TUI's terminal renderer implements it. The relay
// fans the lease holder's frames out to the sink, mirroring the real relay
// which fans out to a display's viewers.
type Sink interface {
	Show(*Frame)
}

// Relay models the display protocol's per-display lease: one holder at a time,
// reserve/granted/busy/renew/release, and a TTL after which the lease lapses
// (capabilities.json `reservation`, max_ttl 900s). It is in-process only; it
// never opens a network connection.
type Relay struct {
	Profile Profile
	sink    Sink

	holder string
	expiry time.Time
	seq    int
	now    func() time.Time // injectable clock for tests
}

// NewRelay builds a relay for a profile, fanning frames out to sink.
func NewRelay(p Profile, sink Sink) *Relay {
	return &Relay{Profile: p, sink: sink, now: time.Now}
}

// Reserve takes the lease for name with the given ttl (seconds), returning the
// granted expiry. It answers "busy" if a different holder's lease is still
// live; re-reserving as the current holder renews it.
func (r *Relay) Reserve(name string, ttl int) (time.Time, error) {
	if ttl <= 0 || ttl > 900 {
		return time.Time{}, fmt.Errorf("bad ttl %d (1..900)", ttl)
	}
	if r.live() && r.holder != name {
		return time.Time{}, fmt.Errorf("busy: display held by %q", r.holder)
	}
	r.holder = name
	r.expiry = r.now().Add(time.Duration(ttl) * time.Second)
	return r.expiry, nil
}

// Renew extends the current holder's lease by ttl seconds (any frame also
// renews; see Send).
func (r *Relay) Renew(name string, ttl int) (time.Time, error) {
	if !r.live() || r.holder != name {
		return time.Time{}, fmt.Errorf("not the lease holder")
	}
	if ttl <= 0 || ttl > 900 {
		return time.Time{}, fmt.Errorf("bad ttl %d (1..900)", ttl)
	}
	r.expiry = r.now().Add(time.Duration(ttl) * time.Second)
	return r.expiry, nil
}

// Release drops the lease if held by name.
func (r *Relay) Release(name string) error {
	if r.holder != name {
		return fmt.Errorf("not the lease holder")
	}
	r.holder = ""
	r.expiry = time.Time{}
	return nil
}

// Send validates a frame from name, renews the lease (a frame is a renewal),
// bumps the sequence, and shows it on the sink. It rejects a frame whose size
// does not match the profile, and a sender that does not hold the lease.
func (r *Relay) Send(name string, f *Frame) error {
	if !r.live() || r.holder != name {
		return fmt.Errorf("not the lease holder (reserve first)")
	}
	if f.W != r.Profile.W || f.H != r.Profile.H {
		return fmt.Errorf("frame is %dx%d, display is %dx%d", f.W, f.H, r.Profile.W, r.Profile.H)
	}
	for i, c := range f.Cells {
		if int(c) >= len(r.Profile.Colors()) {
			return fmt.Errorf("cell %d index %d out of palette range 0..%d", i, c, len(r.Profile.Colors())-1)
		}
	}
	// A frame renews the lease up to the max TTL, as the spec allows.
	r.expiry = r.now().Add(900 * time.Second)
	r.seq++
	r.sink.Show(f)
	return nil
}

// Holder returns the current lease holder ("" if none/lapsed).
func (r *Relay) Holder() string {
	if !r.live() {
		return ""
	}
	return r.holder
}

// TTL returns the seconds remaining on the lease (0 if none).
func (r *Relay) TTL() int {
	if !r.live() {
		return 0
	}
	return int(r.expiry.Sub(r.now()).Seconds())
}

// Seq returns the number of frames sent this session.
func (r *Relay) Seq() int { return r.seq }

func (r *Relay) live() bool {
	return r.holder != "" && r.now().Before(r.expiry)
}
