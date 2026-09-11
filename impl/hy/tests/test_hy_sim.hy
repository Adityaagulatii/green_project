"The Hy simulator: recorder, building model, ANSI renderer, bot and CLI
(SPEC §10). All headless."

(import io json)
(import pytest)
(import tetris_hy.sim.recorder [Recorder record tetris BLACK-FRAME])
(import tetris_hy.sim.building [GREEN-BUILDING check window windows lit-floors])
(import tetris_hy.sim.ansi [ansi-frame frame-lines play SHOW-CURSOR])
(import tetris_hy.sim.bot [make-bot plan])
(import tetris_hy.sim.cli [main])
(import tetris_hy.conformance [run-trace observe])
(import tetris_hy.engine [init step])

;; ------------------------------------------------------------ recorder

(defn test-the-recorder-is-a-display-that-clamps []
  (setv rec (Recorder))
  (assert (= (.makeframe rec) BLACK-FRAME))
  (.send rec (lfor _ (range 17) (lfor _ (range 9) #(300 -5 12.7))))
  (assert (= (get rec.frames 0 0 0) #(255 0 12)))
  (assert (= (len rec) 1))
  (with [(pytest.raises ValueError)]
    (.send rec [[#(0 0 0)]])))

(defn test-a-recorded-run-is-a-trace []
  "§10.2: the input log replays to the same frames."
  (setv [rec state] (record (tetris 7) 400 (make-bot)))
  (assert (= (len rec) 400))
  (assert (= (get (.times rec) 30) 1.0))
  (assert rec.events)
  (setv replay (run-trace {"seed" 7 "frames" 400 "digest_every" 1 "events" rec.events}))
  (assert (= (get replay "digests") (.digests rec)))
  (assert (= (get replay "final") (observe state))))

(defn test-any-animation-can-drive-the-recorder []
  (setv stripes {"init" (fn [] 0)
                 "tick" (fn [t events] (+ t 1))
                 "render" (fn [t] (tuple (gfor r (range 17)
                                               (* #(#((* 15 (% (+ r t) 17)) 0 0)) 9))))}
        [rec t] (record stripes 20))
  (assert (= t 20))
  (assert (= (len (set rec.frames)) 17))
  (assert (= rec.info (* [None] 20))))

;; ------------------------------------------------------------ building

(defn test-the-building-has-153-windows-on-floors-20-to-4 []
  (setv ws (windows))
  (assert (= (len ws) 153))
  (assert (= (len (sfor w ws #((get w "floor") (get w "bay")))) 153))
  (assert (= (lit-floors) (list (range 20 3 -1))))
  (assert (= (window 0 0) {"row" 0 "col" 0 "floor" 20 "bay" 1}))
  (assert (= (window 16 8) {"row" 16 "col" 8 "floor" 4 "bay" 9}))
  (assert (get GREEN-BUILDING "provisional"))
  (with [(pytest.raises IndexError)]
    (window 17 0))
  (with [(pytest.raises ValueError)]
    (check (| GREEN-BUILDING {"cols" 10})))
  (with [(pytest.raises ValueError)]
    (check (| GREEN-BUILDING {"top_floor" 22}))))

;; ------------------------------------------------------------ ANSI

(defn test-ansi-frames []
  (setv frame (tuple (gfor r (range 17) (* #(#(r 2 3)) 9)))
        plain (frame-lines frame)
        facade (frame-lines frame :facade True))
  (assert (= (len plain) (len facade) 17))
  (assert (= (.count (get plain 5) "\x1b[48;2;5;2;3m") 9))
  (assert (.startswith (get facade 0) "F20 "))
  (assert (.startswith (get facade 16) "F 4 "))
  (assert (.endswith (get facade 16) "\x1b[0m"))
  (assert (= (ansi-frame frame) (.join "\n" plain))))

(defn test-play-paces-at-30-fps []
  (setv out (io.StringIO)
        naps []
        frames (* [BLACK-FRAME] 4))
  (play frames :out out :sleep (fn [dt] (.append naps dt)))
  (assert (= naps (* [(/ 1.0 30)] 4)))
  (assert (.endswith (.getvalue out) SHOW-CURSOR))
  (setv naps [])
  (play frames :out (io.StringIO) :speed 0 :sleep (fn [dt] (.append naps dt)))
  (assert (= naps [])))

;; ------------------------------------------------------------ bot

(defn test-the-bot-clears-lines-and-is-deterministic []
  (setv [rec1 s1] (record (tetris 1) 1500 (make-bot))
        [rec2 s2] (record (tetris 1) 1500 (make-bot)))
  (assert (= rec1.events rec2.events))
  (assert (> (max (gfor h rec1.info :if h (get h "score"))) 0)))

(defn test-the-bot-only-plays-while-playing []
  (setv bot (make-bot))
  (assert (= (bot (init 1)) []))
  (assert (is-not (plan (init 1)) None)))

;; ------------------------------------------------------------ CLI

(defn cli-summary [capsys argv]
  (assert (= (main argv) 0))
  (json.loads (get (.splitlines (. (.readouterr capsys) out)) -1)))

(defn test-cli-bot-run [capsys]
  (setv summary (cli-summary capsys ["--seed" "3" "--frames" "200" "--bot-fast"]))
  (assert (in (get summary "phase") #("playing" "clearing" "gameover" "countdown")))
  (assert (>= (get summary "score") 0))
  (assert (not-in "frame_hex" summary)))

(defn test-cli-trace-replay-and-trace-out [capsys tmp-path]
  (setv path (str (/ tmp-path "run.json"))
        summary (cli-summary capsys ["--seed" "5" "--frames" "150" "--bot-fast"
                                     "--trace-out" path]))
  (with [fh (open path)]
    (setv saved (json.load fh)))
  (assert (= (get saved "frames") 150))
  (setv again (cli-summary capsys ["--trace" path]))
  (assert (= again summary))
  (setv final (get (run-trace saved) "final"))
  (del (get final "frame_hex"))
  (assert (= final summary)))

(defn test-cli-ansi-final-and-building [capsys]
  (assert (= (main ["--frames" "95" "--ansi-final"]) 0))
  (setv out (. (.readouterr capsys) out))
  (assert (in "F20 " out))
  (assert (= (main ["--building"]) 0))
  (setv model (json.loads (. (.readouterr capsys) out)))
  (assert (= (len (get model "windows")) 153)))
