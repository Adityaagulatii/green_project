"The Hy engine against the Python reference, frame by frame
(experiments/005-hy-python-differential).

Both engines get the same seed and the same events. After every frame
they must agree on everything the spec makes observable (the frame, the
phase, score, level, lines, high score, and the active and held pieces)
and on the rule state behind it, projected to a common vocabulary: the
padded board, the consumed latches, the DCD counter, the binary64
accumulator (bit for bit), the bag still to draw, the PRNG state, the
input queue, and the suspended rest of a frame."

(import os)
(import dataclasses [replace])
(import hypothesis [given])
(import hypothesis.strategies :as st)
(import pytest)
(import tetris_engine [core :as py])
(import tetris_engine.frame [render-codes :as py-render-codes])
(import tetris_hy.engine [init step phase put apply-event])
(import tetris_hy.frame [render-codes])
(import tetris_hy.tables [ACTIONS BAG-ORDER])
(import tetris_hy.sim.bot [make-bot tap])
(import hy_strategies [seeds booted placed])

(setv LATCHES {"cw" "cw_available"  "ccw" "ccw_available"
               "r180" "r180_available"  "hard" "hard_available"})

(defn hy-view [s]
  (setv program (:suspended s))
  {"phase" (phase s)  "frame" (render-codes s)
   "board" (:board s)  "active" (:active s)  "hold" (:hold s)
   "hold_available" (:hold-ok s)
   "level" (:level s)  "lines" (:lines s)  "score" (:score s)
   "high_score" (:high-score s)
   "acc" (.hex (:acc s))  "dcd" (:dcd s)  "consumed" (frozenset (:spent s))
   "bag_left" (:bag s)  "rng" (:rng s)  "queue" (:queue s)
   "suspended" (when (is-not program None)
                 #((tuple (gfor op program :if (isinstance op tuple) op))
                   (in "level-check" program)))})

(defn py-view [s]
  {"phase" s.phase  "frame" (py-render-codes s)
   "board" s.board  "active" (tuple s.active)
   "hold" (when s.hold (tuple s.hold))
   "hold_available" s.hold-available
   "level" s.level  "lines" s.lines  "score" s.score
   "high_score" s.high-score
   "acc" (.hex s.acc)  "dcd" s.dcd
   "consumed" (frozenset (gfor [name attr] (.items LATCHES) :if (not (getattr s attr)) name))
   "bag_left" (tuple (cut s.bag s.bag-index None))  "rng" s.rng
   "queue" (tuple s.inbox)
   "suspended" (when s.resume #((tuple s.resume.events) s.resume.stages))})

(defn diff [a b]
  (dfor k a :if (!= (get a k) (get b k)) k #((get a k) (get b k))))

(defn agree [h p where]
  (setv hv (hy-view h)
        pv (py-view p))
  (assert (= hv pv) #(where (diff hv pv))))

(defn run-both [seed per-frame [bot False]]
  "Drive both engines from init; they must agree after every frame.
  Returns coverage counts."
  (setv h (init seed)
        p (py.new-game seed)
        b (when bot (make-bot))
        seen {"clears" 0 "game_overs" 0 "max_level" 0})
  (agree h p "init")
  (for [[k evs] (enumerate per-frame)]
    (setv evs (+ (if b (list (b h)) []) (list evs))
          before (phase h)
          h (step h evs)
          p (py.step p evs))
    (agree h p #(k evs))
    (when (and (= (phase h) "clearing") (!= before "clearing"))
      (+= (get seen "clears") 1))
    (when (and (= (phase h) "gameover") (!= before "gameover"))
      (+= (get seen "game_overs") 1))
    (setv (get seen "max_level") (max (get seen "max_level") (:level h))))
  seen)

(setv frame-events (st.lists (st.tuples (st.sampled-from ACTIONS) (st.booleans))
                             :max-size 4))

;; A Hy defn returns its last form, and Hypothesis rejects a test that
;; returns a value: these two end with None.

(defn [(given seeds (st.lists frame-events :max-size 300))]
  test-random-input-agrees [seed frames]
  (run-both seed frames)
  None)

(defn [(given seeds (st.lists (st.one-of (st.just []) frame-events)
                              :min-size 50 :max-size 400))]
  test-bot-plus-noise-agrees [seed frames]
  (run-both seed frames :bot True)
  None)

(defn [(pytest.mark.parametrize "seed" [1 42 2026])] test-long-bot-game-agrees [seed]
  (setv n (if (os.environ.get "TETRIS_SLOW") 3000 700)
        seen (run-both seed (* [[]] n) :bot True))
  (assert (> (get seen "clears") 0)))

;; ------------------------------------------------------------ constructed states

(defn py-booted [seed]
  (setv s (py.new-game seed))
  (for [_ (range 92)]
    (setv s (py.step s)))
  s)

(defn both-at [seed board piece dcd spent held hold-ok]
  "The same mid-game position in both engines."
  #((put (booted seed) :board board :active piece :dcd dcd :spent spent
         :hold held :hold-ok hold-ok)
    (replace (py-booted seed) :board board :active (py.Piece #* piece) :dcd dcd
             :cw-available (not-in "cw" spent) :ccw-available (not-in "ccw" spent)
             :r180-available (not-in "r180" spent) :hard-available (not-in "hard" spent)
             :hold (when held (py.Held #* held)) :hold-available hold-ok)))

(setv positions
  (st.tuples seeds (st.one-of (placed) (placed :almost-full True)) (st.integers 0 4)
             (st.frozensets (st.sampled-from #("cw" "ccw" "r180" "hard")))
             (st.one-of (st.none) (st.tuples (st.sampled-from BAG-ORDER) (st.integers 0 3)))
             (st.booleans)))

(defn [(given positions (st.lists (st.tuples (st.sampled-from ACTIONS) (st.booleans))
                                  :max-size 12))]
  test-rules-agree-event-by-event [position evs]
  (setv [seed [board piece] dcd spent held hold-ok] position
        [h p] (both-at seed board piece dcd spent held hold-ok))
  (agree h p "start")
  (for [[i [action down]] (enumerate evs)]
    (setv h (apply-event h action down)
          p (py.apply-action p action down))
    (agree h p #(i action down))
    (when (!= (phase h) "playing")
      (break))))

(defn [(given positions (st.lists frame-events :max-size 60))]
  test-frames-agree-from-mid-game-positions [position frames]
  (setv [seed [board piece] dcd spent held hold-ok] position
        [h p] (both-at seed board piece dcd spent held hold-ok))
  (for [[k evs] (enumerate frames)]
    (setv h (step h evs)
          p (py.step p evs))
    (agree h p #(k evs))))

;; ------------------------------------------------------------ game-over boundaries

(defn [(given seeds (st.dictionaries (st.integers 0 330) frame-events :max-size 12))]
  test-game-over-boundaries-agree [seed sched]
  "Top out by hard drops, then cross flash → game over → countdown →
  resume with input at random frames: the transitions where §9.2 queues,
  discards and resumes. Random play rarely gets this far."
  (setv h (booted seed)
        p (py-booted seed))
  (agree h p "booted")
  (while (!= (phase h) "gameover")
    (setv h (step h (tap "hard_drop"))
          p (py.step p (tap "hard_drop")))
    (agree h p "top-out"))
  (for [k (range 330)]
    (setv evs (.get sched k [])
          h (step h evs)
          p (py.step p evs))
    (agree h p #(k evs))))
