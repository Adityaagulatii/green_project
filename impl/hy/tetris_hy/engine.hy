"The engine of SPEC.md, re-told in Hy.

  (init seed)          → state      §9.1
  (step state events)  → state      §9.2: one frame, 1/30 s
  (phase state)        → \"countdown\" | \"playing\" | \"clearing\" | \"gameover\"

Rendering is in frame.hy. There is no I/O, clock or global randomness here.

A state is a plain map (a dict) that is never mutated: every change goes
through `put`, which returns a new map. A piece is a tuple #(shape rot row
col), the board is a tuple of 20 strings of 11 cell codes, and the latches
are the set of those that are consumed.

Two ideas carry the re-telling:

- **A logical frame is a program.** It is the queued and fresh events,
  followed by the stages \"level-check\" and \"gravity\". `run` performs it
  op by op. A lock that starts an animation stops the run, and the rest of
  the program is kept in :suspended until the animation ends. This is the
  hidden pause of §9.2 (QUIRK-13): whatever is left of the frame runs
  afterwards, whether the lock came from an event or from gravity.
- **Animations are a script.** A clear and a top-out set a script of
  segments such as #(#(\"flash\" 5) #(\"gameover\" 218) #(\"countdown\" 90)).
  While a script runs, play is paused and the phase is read off its current
  segment. Entering the countdown after a game over resets the game. When
  the script runs out, play resumes."

(require tetris_hy.macros [->])
(import tetris_hy.tables [ROWS COLS HEIGHT WIDTH SPAWN-ROW SPAWN-COL
                          DCD DCD-PER-FRAME ACTIONS CELLS KICKS KICKS-180
                          GRAVITY-DEN FLASH-FRAMES GAMEOVER-FRAMES
                          COUNTDOWN-FRAMES])
(import tetris_hy.prng [seed-state shuffle-bag])

;; ------------------------------------------------------------ maps

(defn put [m #** changes]
  "A new map: `m` with `changes`. `m` itself is left untouched."
  (| m changes))

;; ------------------------------------------------------------ board and pieces

(setv WALL (* "W" WIDTH)
      EMPTY-ROW (+ "W" (* "." COLS) "W")
      EMPTY-BOARD (+ #(WALL) (* #(EMPTY-ROW) ROWS) #(WALL WALL)))

(defn spawn [shape [rot 0]]
  "A piece at the spawn cell, §4.3."
  #(shape rot SPAWN-ROW SPAWN-COL))

(defn moved [piece drow dcol]
  (setv [shape rot row col] piece)
  #(shape rot (+ row drow) (+ col dcol)))

(defn cells [piece]
  "The board cells the piece covers, row-major."
  (setv [shape rot row col] piece)
  (lfor [i j] (get CELLS shape rot) #((+ row i) (+ col j))))

(defn collides? [board piece]
  "§4.4: a cell outside the padded board, or on a non-empty cell."
  (for [[r c] (cells piece)]
    (when (or (not (<= 0 r (- HEIGHT 1)))
              (not (<= 0 c (- WIDTH 1)))
              (!= (get board r c) "."))
      (return True)))
  False)

(defn landed [board piece]
  "The piece moved down while the next row is free: both the ghost (§8.1)
  and where a hard drop locks (§5.5)."
  (while (not (collides? board (moved piece 1 0)))
    (setv piece (moved piece 1 0)))
  piece)

(defn paint [board cells code]
  "A new board with `cells` set to `code`."
  (setv rows (list board))
  (for [[r c] cells]
    (setv row (get rows r)
          (get rows r) (+ (cut row 0 c) code (cut row (+ c 1) None))))
  (tuple rows))

(defn full? [row]
  (not-in "." (cut row 1 (+ COLS 1))))

;; ------------------------------------------------------------ scripts and phases

(setv BOOT #(#("countdown" COUNTDOWN-FRAMES) #("black" 1))
      FLASH #(#("flash" FLASH-FRAMES))
      GAME-OVER #(#("gameover" GAMEOVER-FRAMES) #("countdown" COUNTDOWN-FRAMES))
      PHASE-OF {"countdown" "countdown"  "black" "countdown"
                "flash" "clearing"  "gameover" "gameover"}
      STAGES #("level-check" "gravity"))

(defn phase [s]
  (if (:script s) (get PHASE-OF (get (:script s) 0 0)) "playing"))

;; ------------------------------------------------------------ games

(defn new-game [rng]
  "The fields every game starts with (§8.3). What carries over between
  games is left to the caller."
  (setv [bag rng] (shuffle-bag rng))
  {"board" EMPTY-BOARD  "active" (spawn (get bag 0))  "bag" (cut bag 1 None)
   "rng" rng  "hold" None  "hold_ok" True  "spent" (frozenset)  "queue" #()
   "level" 0  "lines" 0  "score" 0  "flash" None  "over_board" None})

(defn init [seed]
  "S0, just before frame 0 (§9.1): the boot countdown is about to start."
  (| (new-game (seed-state seed))
     {"script" BOOT  "t" -1  "suspended" None
      "high_score" 0  "acc" 0.0  "dcd" DCD  "next_dcd" DCD  "drawn" 1}))

(defn reset [s]
  "§8.3: the next game, from the continuing PRNG stream. The high score,
  acc, the PRNG and the suspended frame carry over; dcd is QUIRK-11's."
  (| s (new-game (:rng s)) {"dcd" (:next-dcd s)  "drawn" (+ (:drawn s) 1)}))

(defn draw [s]
  "The next piece of the bag becomes active. An empty bag is refilled
  first, so the refill is lazy (§9.3)."
  (setv [bag rng] (if (:bag s) #((:bag s) (:rng s)) (shuffle-bag (:rng s))))
  (put s :active (spawn (get bag 0)) :bag (cut bag 1 None) :rng rng
         :drawn (+ (:drawn s) 1)))

;; ------------------------------------------------------------ the step, §9.2

(defn checked [events]
  "The events as #(action down) pairs. An unknown action is an error."
  (setv out [])
  (for [[action down] events]
    (when (not-in action ACTIONS)
      (raise (ValueError (+ "unknown action " (repr action)))))
    (.append out #(action (bool down))))
  (tuple out))

(defn step [s [events #()]]
  "Advance one frame, with the events delivered at it, in order."
  (setv events (checked events))
  (if (:script s)
      (animate s events)
      (logical-frame s events)))

(defn animate [s events]
  "One frame of the running script. Input is queued during a flash and
  discarded during a game over or a countdown (QUIRK-12). The frame that
  ends the script resumes play."
  (setv [[kind length] #* later] (:script s)
        t (+ (:t s) 1)
        queue (if (= kind "flash") (+ (:queue s) events) (:queue s)))
  (cond
    (< t length)
      (put s :t t :queue queue)
    later
      (let [s (put s :script (tuple later) :t 0 :queue queue)]
        ;; the countdown after a game over starts the next game
        (if (= (get later 0 0) "countdown") (reset s) s))
    True
      (resume (put s :script #() :t 0) events)))

(defn resume [s events]
  "Play resumes. After the boot countdown a fresh logical frame starts.
  After a flash or a game over, the suspended frame finishes first
  (QUIRK-13), and this frame's events wait in the queue for the next one."
  (setv program (:suspended s))
  (if (is program None)
      (logical-frame s events)
      (run (put s :queue (+ (:queue s) events) :suspended None) program)))

(defn logical-frame [s events]
  "Stage 1, then the frame's program: the queue, the fresh events, the
  level check and gravity."
  (run (put s :acc (+ (:acc s) (gravity-step (:level s)))
              :dcd (+ (:dcd s) DCD-PER-FRAME)
              :queue #())
       (+ (:queue s) events STAGES)))

(defn run [s program]
  "Perform a frame's program op by op. A lock that starts an animation
  suspends the frame: the rest of the program waits in :suspended."
  (for [[i op] (enumerate program)]
    (setv s (perform s op))
    (when (:script s)
      (return (put s :suspended (cut program (+ i 1) None)))))
  s)

(defn perform [s op]
  (cond (= op "level-check") (level-check s)
        (= op "gravity") (gravity s)
        True (let [[action down] op] (apply-event s action down))))

;; ------------------------------------------------------------ levels and gravity, §7

(defn level-target [level]
  "QUIRK-7: the legacy's testing formula, which is level + 5 for level ≥ 0."
  (min (+ level 5) (max 100 (- (* 5 level) 50))))

(defn level-check [s]
  "At most one level per logical frame (QUIRK-16)."
  (setv target (level-target (:level s)))
  (if (<= target (:lines s))
      (put s :level (+ (:level s) 1) :lines (- (:lines s) target))
      s))

(defn gravity-step [level]
  "The accumulator increment (1/DEN)·2, in binary64 (Python floats are)."
  (* (/ 1.0 (get GRAVITY-DEN (min level 29))) 2.0))

(defn gravity [s]
  "QUIRK-8: a full accumulator resets to 0, not to acc − 1, and moves the
  piece one row down."
  (if (>= (:acc s) 1.0)
      (move-down (put s :acc 0.0))
      s))

;; ------------------------------------------------------------ locking, §6

(defn lock [s piece [hard False]]
  "Write the piece into the board, clear full rows, spawn the next piece
  and check for game over. A clear or a top-out starts a script (the flash,
  then the game over), which suspends the rest of the frame."
  (setv board (paint (:board s) (cells piece) (get piece 0))
        full (lfor r (range 1 (+ ROWS 1)) :if (full? (get board r)) r)
        lines (:lines s)
        score (:score s))
  (for [_ full]                    ; QUIRK-5: row by row, per-level counter
    (setv lines (+ lines 1)
          score (+ score (* 100 (+ (// lines 10) 1)))))
  (setv flash (when full
                (tuple (gfor [r row] (enumerate board) (if (in r full) WALL row))))
        board (if full
                  (+ #((get board 0))
                     (* #(EMPTY-ROW) (len full))
                     (tuple (gfor r (range 1 (+ ROWS 1)) :if (not-in r full)
                                  (get board r)))
                     (cut board (+ ROWS 1) None))
                  board)
        s (draw (put s :board board :lines lines :score score :hold-ok True))
        over (collides? board (:active s)))
  (put s :script (+ (if full FLASH #()) (if over GAME-OVER #()))
         :t 0
         :flash flash
         #** (if over
                 {"over_board" board
                  "high_score" (max (:high-score s) score)
                  "next_dcd" (if hard 0 DCD)}      ; QUIRK-11
                 {})))

;; ------------------------------------------------------------ actions, §5

(defn shift [s dcol]
  (setv piece (moved (:active s) 0 dcol))
  (if (collides? (:board s) piece) s (put s :active piece)))

(defn move-down [s]
  "One row down, or lock where it stands: there is no lock delay (QUIRK-14)."
  (setv piece (moved (:active s) 1 0))
  (if (collides? (:board s) piece)
      (lock s (:active s))
      (put s :active piece)))

(defn rotate [s turn source]
  "§5.3: SRS offset tests against `source` for the current state and KICKS
  for the new one. The first free candidate wins and resets DCD; if none is
  free, nothing moves."
  (setv [shape rot row col] (:active s)
        new (% (+ rot turn) 4))
  (for [[[sx sy] [tx ty]] (zip (get source shape rot) (get KICKS shape new))]
    (setv piece #(shape new (- row (- sy ty)) (+ col (- sx tx))))
    (when (not (collides? (:board s) piece))
      (return (put s :active piece :dcd 0))))
  s)

(defn hard-drop [s]
  "§5.5: lock on the ghost."
  (-> s
      (lock (landed (:board s) (:active s)) :hard True)
      (put :hold-ok True :dcd 0)))

(defn hold-piece [s]
  "§5.6: once per locked piece. The held piece keeps its rotation, and the
  piece swapped in is not collision-checked (QUIRK-6)."
  (if (not (:hold-ok s))
      s
      (let [[shape rot _ _] (:active s)
            s (if (:hold s) (put s :active (spawn #* (:hold s))) (draw s))]
        (put s :hold #(shape rot) :hold-ok False))))

(setv GATED
  ;; action          latch   release resets dcd   effect, once the gate allows
  {"rotate_cw"   #("cw"    False                (fn [s] (rotate s 1 KICKS)))
   "rotate_ccw"  #("ccw"   True                 (fn [s] (rotate s 3 KICKS)))       ; QUIRK-3
   "rotate_180"  #("r180"  False                (fn [s] (rotate s 2 KICKS-180)))   ; QUIRK-1
   "hard_drop"   #("hard"  True                 hard-drop)})                       ; QUIRK-4

(setv PRESSES
  ;; ungated: act on the press, ignore the release
  {"left"       (fn [s] (shift s -1))
   "right"      (fn [s] (shift s 1))
   "soft_drop"  move-down
   "hold"       hold-piece})

(defn gated [s action down]
  "§5.4: rotations and the hard drop have a latch and a DCD gate. A press
  consumes the latch even when the gate or the kicks block it (QUIRK-2).
  The release frees it."
  (setv [latch release-resets effect] (get GATED action)
        spent (:spent s))
  (cond
    (and down (not-in latch spent))
      (let [s (put s :spent (| spent #{latch}))]
        (if (>= (:dcd s) DCD) (effect s) s))
    (and (not down) (in latch spent))
      (put s :spent (- spent #{latch})
             :dcd (if release-resets 0 (:dcd s)))
    True s))

(defn apply-event [s action down]
  "One input event on a playing state."
  (cond (in action GATED) (gated s action down)
        down ((get PRESSES action) s)
        True s))
