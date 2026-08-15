// Copyright (c) Tailscale Inc & contributors
// SPDX-License-Identifier: BSD-3-Clause

// Custom WebAssembly entry for the WebVM/CheerpX runtime glue
// (plans/networking-bug.md §15/§16): replaces the stock tsconnect API
// (internal netstack, string states, no tun) with the API surface the
// CheerpX tailscale_tun.js glue requires:
//
//	newIPN(conf) -> { tun, run, up, down, login, logout }
//	  tun:  { onmessage, postMessage(data, transfer) }  (raw IP packets)
//	  run({notifyState, notifyNetMap, notifyBrowseToURL})
//	  notifyState: NUMERIC ipn.State (0=NoState ... 6=Running)
//	  notifyNetMap: JSON string { self: {addresses, ...}, peers: [...] }
//	  up(conf): start/restart the backend with the given settings
//	  down():   stop running (WantRunning=false)
//
// Built with: GOOS=js GOARCH=wasm go build -o tailscale.wasm ./cmd/tsconnect/wasm
// (see scripts/rebuild-tailscale-wasm.sh; ship the matching wasm_exec.js).
package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math/rand/v2"
	"net/netip"
	"os"
	"strings"
	"sync"
	"syscall/js"
	"time"

	"github.com/tailscale/wireguard-go/tun"
	"tailscale.com/control/controlclient"
	"tailscale.com/ipn"
	"tailscale.com/ipn/ipnauth"
	"tailscale.com/ipn/ipnlocal"
	"tailscale.com/ipn/ipnserver"
	"tailscale.com/ipn/store/mem"
	"tailscale.com/logpolicy"
	"tailscale.com/logtail"
	"tailscale.com/net/netns"
	"tailscale.com/net/tsdial"
	"tailscale.com/safesocket"
	"tailscale.com/tailcfg"
	"tailscale.com/tsd"
	"tailscale.com/types/logger"
	"tailscale.com/types/views"
	"tailscale.com/wgengine"
	"tailscale.com/words"
)

// ControlURL defines the URL to be used for connection to Control.
var ControlURL = ipn.DefaultControlURL

func main() {
	js.Global().Set("newIPN", js.FuncOf(func(this js.Value, args []js.Value) any {
		if len(args) < 1 {
			log.Printf("Usage: newIPN(config)")
			return nil
		}
		return newIPN(args[0])
	}))
	// Keep Go runtime alive, otherwise it will be shut down before newIPN gets
	// called.
	<-make(chan bool)
}

func newIPN(jsConfig js.Value) map[string]any {
	netns.SetEnabled(false)

	var store ipn.StateStore
	if jsStateStorage := jsConfig.Get("stateStorage"); !jsStateStorage.IsUndefined() {
		store = &jsStateStore{jsStateStorage}
	} else {
		store = new(mem.Store)
	}

	controlURL := ControlURL
	// The CheerpX glue passes "controlUrl" (lowercase U); the stock tsconnect
	// JS API uses "controlURL". Accept both.
	if jsControlURL := jsConfig.Get("controlUrl"); jsControlURL.Type() == js.TypeString {
		controlURL = jsControlURL.String()
	} else if jsControlURL := jsConfig.Get("controlURL"); jsControlURL.Type() == js.TypeString {
		controlURL = jsControlURL.String()
	}

	var authKey string
	if jsAuthKey := jsConfig.Get("authKey"); jsAuthKey.Type() == js.TypeString {
		authKey = jsAuthKey.String()
	}

	var hostname string
	if jsHostname := jsConfig.Get("hostname"); jsHostname.Type() == js.TypeString {
		hostname = jsHostname.String()
	} else {
		hostname = generateHostname()
	}

	// No logtail uploads: this build serves a fully self-hosted, LAN-only
	// tailnet and must not phone Tailscale's public log server (the site CSP
	// blocks it anyway). The logpolicy config is kept only for a stable
	// backend log id.
	lpc := getOrCreateLogPolicyConfig(store)
	logf := log.Printf

	sys := tsd.NewSystem()
	sys.Set(store)
	dialer := &tsdial.Dialer{Logf: logf}
	dialer.SetBus(sys.Bus.Get())

	jt := newJSTun(logf)
	eng, err := wgengine.NewUserspaceEngine(logf, wgengine.Config{
		Tun:           jt,
		Dialer:        dialer,
		SetSubsystem:  sys.Set,
		ControlKnobs:  sys.ControlKnobs(),
		HealthTracker: sys.HealthTracker.Get(),
		ExtraRootCAs:  sys.ExtraRootCAs,
		Metrics:       sys.UserMetricsRegistry(),
		EventBus:      sys.Bus.Get(),
	})
	if err != nil {
		log.Printf("wgengine.NewUserspaceEngine: %v", err)
		return nil
	}
	sys.Set(eng)
	sys.Tun.Get().Start()

	logid := lpc.PublicID
	srv := ipnserver.New(logf, logid, sys.Bus.Get(), sys.NetMon.Get())
	lb, err := ipnlocal.NewLocalBackend(logf, logid, sys, controlclient.LoginEphemeral)
	if err != nil {
		log.Printf("ipnlocal.NewLocalBackend: %v", err)
		return nil
	}
	srv.SetLocalBackend(lb)

	jsIPN := &jsIPN{
		srv:        srv,
		lb:         lb,
		controlURL: controlURL,
		authKey:    authKey,
		hostname:   hostname,
	}

	return map[string]any{
		"tun": jt.obj,
		"run": js.FuncOf(func(this js.Value, args []js.Value) any {
			if len(args) != 1 {
				log.Fatal(`Usage: run({
					notifyState(state: int): void,
					notifyNetMap(netMap: string): void,
					notifyBrowseToURL(url: string): void,
					notifyPanicRecover(err: string): void,
				})`)
				return nil
			}
			jsIPN.run(args[0])
			return nil
		}),
		"up": js.FuncOf(func(this js.Value, args []js.Value) any {
			if len(args) != 1 {
				log.Printf("Usage: up(config)")
				return nil
			}
			jsIPN.up(args[0])
			return nil
		}),
		"down": js.FuncOf(func(this js.Value, args []js.Value) any {
			jsIPN.down()
			return nil
		}),
		"login": js.FuncOf(func(this js.Value, args []js.Value) any {
			if len(args) != 0 {
				log.Printf("Usage: login()")
				return nil
			}
			jsIPN.login()
			return nil
		}),
		"logout": js.FuncOf(func(this js.Value, args []js.Value) any {
			if len(args) != 0 {
				log.Printf("Usage: logout()")
				return nil
			}
			jsIPN.logout()
			return nil
		}),
	}
}

type jsIPN struct {
	srv        *ipnserver.Server
	lb         *ipnlocal.LocalBackend
	controlURL string
	authKey    string
	hostname   string

	startOnce sync.Once
}

// run wires the JS callbacks. Unlike the stock tsconnect entry, it does NOT
// start the backend: the CheerpX glue drives the client via up(settings)
// (notifyState(NoState) -> up(conf) is the runtime's expected sequence).
func (i *jsIPN) run(jsCallbacks js.Value) {
	notifyState := func(state ipn.State) {
		// Numeric state per the CheerpX glue's State enum (0-6).
		jsCallbacks.Call("notifyState", int(state))
	}
	notifyState(ipn.NoState)

	i.lb.SetNotifyCallback(func(n ipn.Notify) {
		// Panics in the notify callback are likely to be due to bugs in
		// this bridging module (as opposed to actual bugs in Tailscale) and
		// thus may be recoverable. Let the UI know, and allow the user to
		// choose if they want to reload the page.
		defer func() {
			if r := recover(); r != nil {
				fmt.Println("Panic recovered:", r)
				jsCallbacks.Call("notifyPanicRecover", fmt.Sprint(r))
			}
		}()
		// Summarize the notify instead of dumping the full netmap (peer
		// keys/addresses) to the browser console on every change.
		log.Printf("NOTIFY: state=%v selfChange=%v browseToURL=%v health=%v", n.State != nil, n.SelfChange != nil, n.BrowseToURL != nil, n.Health != nil)
		if n.State != nil {
			notifyState(*n.State)
		}
		if n.SelfChange != nil {
			// Self changed: rebuild the JS-side NetMap snapshot. Peers
			// don't ride on the bus anymore, so fetch them on demand
			// from LocalBackend.
			nm := i.lb.NetMapWithPeers()
			if nm != nil {
				jsNetMap := jsNetMap{
					Self: jsNetMapSelfNode{
						jsNetMapNode: jsNetMapNode{
							Name:       nm.SelfName(),
							Addresses:  mapSliceView(nm.GetAddresses(), func(a netip.Prefix) string { return a.Addr().String() }),
							NodeKey:    nm.NodeKey.String(),
							MachineKey: nm.MachineKey.String(),
						},
						MachineStatus: jsMachineStatus[nm.GetMachineStatus()],
					},
					Peers: mapSlice(nm.Peers, func(p tailcfg.NodeView) jsNetMapPeerNode {
						name := p.Name()
						if name == "" {
							// In practice this should only happen for Hello.
							name = p.Hostinfo().Hostname()
						}
						addrs := make([]string, p.Addresses().Len())
						for i, ap := range p.Addresses().All() {
							addrs[i] = ap.Addr().String()
						}
						exitNode := false
						for _, cap := range p.Capabilities().All() {
							if cap == tailcfg.NodeAttrSuggestExitNode || cap == tailcfg.NodeAttrAutoExitNode {
								exitNode = true
								break
							}
						}
						return jsNetMapPeerNode{
							jsNetMapNode: jsNetMapNode{
								Name:       name,
								Addresses:  addrs,
								MachineKey: p.Machine().String(),
								NodeKey:    p.Key().String(),
							},
							Online:              p.Online().Clone(),
							TailscaleSSHEnabled: p.Hostinfo().TailscaleSSHEnabled(),
							ExitNode:            exitNode,
						}
					}),
					LockedOut: nm.TKAEnabled && nm.SelfNode.KeySignature().Len() == 0,
				}
				if jsonNetMap, err := json.Marshal(jsNetMap); err == nil {
					jsCallbacks.Call("notifyNetMap", string(jsonNetMap))
				} else {
					log.Printf("Could not generate JSON netmap: %v", err)
				}
			}
		}
		if n.BrowseToURL != nil {
			jsCallbacks.Call("notifyBrowseToURL", *n.BrowseToURL)
		}
	})

	go func() {
		ln, err := safesocket.Listen("")
		if err != nil {
			log.Printf("safesocket.Listen: %v", err)
			return
		}
		if err := i.srv.Run(context.Background(), ln); err != nil {
			log.Printf("ipnserver.Run exited: %v", err)
		}
	}()
}

// up starts the backend on first call and applies exit-node changes on
// subsequent calls. The CheerpX glue calls it with the settings object
// {controlUrl, dnsIp, authKey, exitNodeIp, wantsRunning, ipMap}.
func (i *jsIPN) up(conf js.Value) {
	controlURL := i.controlURL
	if c := conf.Get("controlUrl"); c.Type() == js.TypeString && c.String() != "" {
		controlURL = c.String()
	}
	authKey := i.authKey
	if a := conf.Get("authKey"); a.Type() == js.TypeString {
		authKey = a.String()
	}
	hostname := i.hostname
	if h := conf.Get("hostname"); h.Type() == js.TypeString && h.String() != "" {
		hostname = h.String()
	}
	var exitNodeIP netip.Addr
	if e := conf.Get("exitNodeIp"); e.Type() == js.TypeString && e.String() != "" {
		if ip, err := netip.ParseAddr(e.String()); err == nil {
			exitNodeIP = ip
		} else {
			log.Printf("up: bad exitNodeIp %q: %v", e.String(), err)
		}
	}

	i.startOnce.Do(func() {
		log.Printf("up: starting backend controlURL=%q authKeyLen=%d hostname=%q exitNode=%v state=%v", controlURL, len(authKey), hostname, exitNodeIP, i.lb.State())
		prefs := ipn.Prefs{
			ControlURL:  controlURL,
			RouteAll:    false,
			WantRunning: true,
			Hostname:    hostname,
		}
		if exitNodeIP.IsValid() {
			prefs.ExitNodeIP = exitNodeIP
		}
		go func() {
			if err := i.lb.Start(ipn.Options{
				UpdatePrefs: &prefs,
				AuthKey:     authKey,
			}); err != nil {
				log.Printf("Start error: %v", err)
				return
			}
			// Mirror `tailscale up`: with no node key yet, Start() does not
			// begin the auth flow (cc.Login is only called from startLocked
			// when a node key exists or a config file is set), so the client
			// parks in NeedsLogin. StartLoginInteractive starts the auth
			// routine, which consumes the preauth key from the control
			// client options and registers non-interactively.
			if i.lb.State() == ipn.NeedsLogin {
				log.Printf("up: triggering login flow (auth key present)")
				if err := i.lb.StartLoginInteractive(context.Background()); err != nil {
					log.Printf("StartLoginInteractive error: %v", err)
				}
			}
		}()
	})

	if exitNodeIP.IsValid() && i.lb.State() != ipn.NoState {
		go func() {
			if _, err := i.lb.EditPrefs(&ipn.MaskedPrefs{
				Prefs:         ipn.Prefs{ExitNodeIP: exitNodeIP},
				ExitNodeIPSet: true,
			}); err != nil {
				log.Printf("up: EditPrefs exit node error: %v", err)
			}
		}()
	}
}

func (i *jsIPN) down() {
	go func() {
		if _, err := i.lb.EditPrefs(&ipn.MaskedPrefs{
			Prefs:           ipn.Prefs{WantRunning: false},
			WantRunningSet:  true,
		}); err != nil {
			log.Printf("down error: %v", err)
		}
	}()
}

func (i *jsIPN) login() {
	go i.lb.StartLoginInteractive(context.Background())
}

func (i *jsIPN) logout() {
	if i.lb.State() == ipn.NoState {
		log.Printf("Backend not running")
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := i.lb.Logout(ctx, ipnauth.Self); err != nil {
			log.Printf("logout error: %v", err)
		}
	}()
}

var jsMachineStatus = map[tailcfg.MachineStatus]string{
	tailcfg.MachineUnknown:      "MachineUnknown",
	tailcfg.MachineUnauthorized: "MachineUnauthorized",
	tailcfg.MachineAuthorized:   "MachineAuthorized",
	tailcfg.MachineInvalid:      "MachineInvalid",
}

type jsNetMap struct {
	Self      jsNetMapSelfNode   `json:"self"`
	Peers     []jsNetMapPeerNode `json:"peers"`
	LockedOut bool               `json:"lockedOut"`
}

type jsNetMapNode struct {
	Name       string   `json:"name"`
	Addresses  []string `json:"addresses"`
	MachineKey string   `json:"machineKey"`
	NodeKey    string   `json:"nodeKey"`
}

type jsNetMapSelfNode struct {
	jsNetMapNode
	MachineStatus string `json:"machineStatus"`
}

type jsNetMapPeerNode struct {
	jsNetMapNode
	Online              *bool `json:"online,omitempty"`
	TailscaleSSHEnabled bool  `json:"tailscaleSSHEnabled"`
	ExitNode            bool  `json:"exitNode"`
}

type jsStateStore struct {
	jsStateStorage js.Value
}

func (s *jsStateStore) ReadState(id ipn.StateKey) ([]byte, error) {
	jsValue := s.jsStateStorage.Call("getState", string(id))
	if jsValue.String() == "" {
		return nil, ipn.ErrStateNotExist
	}
	return hex.DecodeString(jsValue.String())
}

func (s *jsStateStore) WriteState(id ipn.StateKey, bs []byte) error {
	s.jsStateStorage.Call("setState", string(id), hex.EncodeToString(bs))
	return nil
}

// jsTun implements the wireguard-go tun.Device interface, bridging raw IP
// packets to/from the CheerpX ipstack through a JS object shaped like a
// MessageChannel: JS calls postMessage(data, transfer) to hand packets TO the
// engine (they are read via Read), and the engine writes packets to JS by
// invoking the onmessage callback with {data: Uint8Array}.
type jsTun struct {
	logf logger.Logf

	obj  js.Value // the JS object returned as ipn.tun
	post js.Func  // js.Func backing obj.postMessage; must stay alive
	mtu  int

	packets chan []byte
	closed  chan struct{}

	closeOnce sync.Once
	evCh      chan tun.Event

	drops      int64 // dropped packets since the last rate-limited log
	dropsSince time.Time
}

func newJSTun(logf logger.Logf) *jsTun {
	t := &jsTun{
		logf:    logf,
		mtu:     1420,
		packets: make(chan []byte, 256),
		closed:  make(chan struct{}),
		evCh:    make(chan tun.Event, 16),
	}
	t.post = js.FuncOf(func(this js.Value, args []js.Value) any {
		if len(args) < 1 || args[0].Type() != js.TypeObject {
			return nil
		}
		data := args[0]
		buf := make([]byte, data.Length())
		js.CopyBytesToGo(buf, data)
		select {
		case t.packets <- buf:
		case <-t.closed:
		default:
			// Rate-limit the drop log: this runs on the JS->Go receive
			// hot path when the queue is full (the overload case).
			t.drops++
			if t.dropsSince.IsZero() {
				t.dropsSince = time.Now()
			}
			if time.Since(t.dropsSince) >= time.Second {
				logf("jsTun: dropping packets (queue full): %d in last 1s", t.drops)
				t.drops = 0
				t.dropsSince = time.Time{}
			}
		}
		return nil
	})
	t.obj = js.ValueOf(map[string]any{
		"postMessage": t.post,
	})
	return t
}

func (t *jsTun) Read(bufs [][]byte, sizes []int, offset int) (int, error) {
	select {
	case pkt := <-t.packets:
		if len(bufs) == 0 || len(bufs[0]) <= offset {
			return 0, errors.New("jsTun: no buffer")
		}
		n := copy(bufs[0][offset:], pkt)
		sizes[0] = n
		return 1, nil
	case <-t.closed:
		return 0, io.EOF
	}
}

func (t *jsTun) Write(bufs [][]byte, offset int) (int, error) {
	cb := t.obj.Get("onmessage")
	if cb.Type() != js.TypeFunction {
		// The JS glue installs ipn.tun.onmessage before any traffic flows;
		// if it is missing, drop the packet rather than crash.
		return len(bufs), nil
	}
	for _, buff := range bufs {
		data := buff[offset:]
		u8 := js.Global().Get("Uint8Array").New(len(data))
		js.CopyBytesToJS(u8, data)
		cb.Invoke(js.ValueOf(map[string]any{"data": u8}))
	}
	return len(bufs), nil
}

func (t *jsTun) MTU() (int, error)     { return t.mtu, nil }
func (t *jsTun) Name() (string, error) { return "js", nil }
func (t *jsTun) File() *os.File        { return nil }
func (t *jsTun) Events() <-chan tun.Event {
	return t.evCh
}
func (t *jsTun) BatchSize() int { return 1 }

func (t *jsTun) Close() error {
	t.closeOnce.Do(func() {
		close(t.closed)
		close(t.evCh)
		t.post.Release()
	})
	return nil
}

func mapSlice[T any, M any](a []T, f func(T) M) []M {
	n := make([]M, len(a))
	for i, e := range a {
		n[i] = f(e)
	}
	return n
}

func mapSliceView[T any, M any](a views.Slice[T], f func(T) M) []M {
	n := make([]M, a.Len())
	for i, v := range a.All() {
		n[i] = f(v)
	}
	return n
}

func filterSlice[T any](a []T, f func(T) bool) []T {
	n := make([]T, 0, len(a))
	for _, e := range a {
		if f(e) {
			n = append(n, e)
		}
	}
	return n
}

func generateHostname() string {
	tails := words.Tails()
	scales := words.Scales()
	if rand.IntN(2) == 0 {
		// JavaScript
		tails = filterSlice(tails, func(s string) bool { return strings.HasPrefix(s, "j") })
		scales = filterSlice(scales, func(s string) bool { return strings.HasPrefix(s, "s") })
	} else {
		// WebAssembly
		tails = filterSlice(tails, func(s string) bool { return strings.HasPrefix(s, "w") })
		scales = filterSlice(scales, func(s string) bool { return strings.HasPrefix(s, "a") })
	}

	tail := tails[rand.IntN(len(tails))]
	scale := scales[rand.IntN(len(scales))]
	return fmt.Sprintf("%s-%s", tail, scale)
}

const logPolicyStateKey = "log-policy"

func getOrCreateLogPolicyConfig(state ipn.StateStore) *logpolicy.Config {
	if configBytes, err := state.ReadState(logPolicyStateKey); err == nil {
		if config, err := logpolicy.ConfigFromBytes(configBytes); err == nil {
			return config
		} else {
			log.Printf("Could not parse log policy config: %v", err)
		}
	} else if err != ipn.ErrStateNotExist {
		log.Printf("Could not get log policy config from state store: %v", err)
	}
	config := logpolicy.NewConfig(logtail.CollectionNode)
	if err := state.WriteState(logPolicyStateKey, config.ToBytes()); err != nil {
		log.Printf("Could not save log policy config to state store: %v", err)
	}
	return config
}
