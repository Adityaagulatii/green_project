"SPEC §11 properties P1–P18, re-told in Hy. P19 is in test_hy_kat.hy.

\"Within a game\" means between two resets; a reset is the step from the
game-over sequence into the countdown that follows it."

(import copy)
(import hypothesis [given assume])
(import hypothesis.strategies :as st)
(import tetris_hy.engine [init step phase put collides? landed cells draw
                          apply-event EMPTY-BOARD WALL FLASH])
(import tetris_hy.frame [render render-codes digest BLACK])
(import tetris_hy.tables [PALETTE BAG-ORDER GRAVITY-DEN GLYPHS])
(import tetris_hy.sim.bot [make-bot tap])
(import hy_strategies [seeds events schedules booted trajectory placed])

(setv PALETTE-COLORS (set (.values PALETTE))
      ;; SPEC §7.3: frames between drops from acc = 0, per DEN
      CADENCE {48 25  43 22  38 20  33 17  28 15  23 12  18 9  13 7  8 4
               6 3  5 3  4 2  3 2  2 1  1 1}
      ROTATIONS #("rotate_cw" "rotate_ccw" "rotate_180"))

(defn new-game? [prev nxt]
  (and (= (phase prev) "gameover") (= (phase nxt) "countdown")))

(defn game-over-started? [s]
  (in "gameover" (gfor [kind _] (:script s) kind)))

;; ------------------------------------------------------------ P1 frame contract

(defn [(given seeds (schedules))] test-p01-frame-contract [seed sch]
  (setv [n sched bot] sch)
  (for [[_ _ s] (trajectory seed n sched bot :boot False)]
    (setv frame (render s))
    (assert (= (len frame) 17))
    (for [row frame]
      (assert (= (len row) 9))
      (for [rgb row]
        (assert (all (gfor v rgb (and (isinstance v int) (<= 0 v 255)))))
        (assert (in rgb PALETTE-COLORS))))))

;; ------------------------------------------------------------ P2 purity and determinism

(defn [(given seeds (schedules 120))] test-p02-purity-and-determinism [seed sch]
  (setv [n sched bot] sch
        digests [])
  (for [[prev evs nxt] (trajectory seed n sched bot)]
    (setv snapshot (copy.deepcopy prev))
    (assert (= (step prev evs) nxt))
    (assert (= prev snapshot) "step mutated its input")
    (.append digests (digest (render-codes nxt))))
  (assert (= digests (lfor [_ _ s] (trajectory seed n sched bot)
                           (digest (render-codes s))))))

;; ------------------------------------------------------------ P3 board frame

(defn [(given seeds (schedules))] test-p03-board-frame [seed sch]
  (setv [n sched bot] sch)
  (for [[_ _ s] (trajectory seed n sched bot)]
    (setv board (:board s))
    (assert (= (len board) 20))
    (assert (all (gfor row board (= (len row) 11))))
    (assert (= (get board 18) (get board 19) WALL))
    (assert (all (gfor row board (= (get row 0) (get row 10) "W"))))
    (assert (not-in "." (get board 0)))))

;; ------------------------------------------------------------ P4 legal active piece

(defn [(given seeds (schedules))] test-p04-legal-active-piece [seed sch]
  (setv [n sched bot] sch)
  (for [[_ _ s] (trajectory seed n sched bot)]
    (when (= (phase s) "playing")
      (for [[r c] (cells (:active s))]
        (assert (and (<= 0 r 19) (<= 0 c 10))))
      (when (collides? (:board s) (:active s))
        ;; only a hold since the last lock can have put it there (QUIRK-6)
        (assert (not (:hold-ok s)))))))

;; ------------------------------------------------------------ P5 no full rows at rest

(defn [(given seeds (schedules))] test-p05-no-full-rows-at-rest [seed sch]
  (setv [n sched bot] sch)
  (for [[_ _ s] (trajectory seed n sched bot)]
    (for [r (range 1 18)]
      (assert (in "." (cut (get (:board s) r) 1 10))))))

;; ------------------------------------------------------------ P6 7-bag

(defn [(given seeds)] test-p06-the-piece-stream-is-bags [seed]
  (setv s (init seed)
        drawn [(get (:active s) 0)])
  (for [_ (range 69)]
    (setv s (draw s))
    (.append drawn (get (:active s) 0)))
  (for [k (range 0 70 7)]
    (assert (= (sorted (cut drawn k (+ k 7))) (sorted BAG-ORDER)))))

(defn [(given seeds (schedules))] test-p06-bag-state-and-a-fresh-bag-per-game [seed sch]
  (setv [n sched bot] sch)
  (for [[prev _ s] (trajectory seed n sched bot)]
    (setv left (:bag s))
    (assert (<= (len left) 6))
    (assert (= (len (set left)) (len left)))
    (assert (<= (set left) (set BAG-ORDER)))
    (when (new-game? prev s)
      (assert (= (sorted (+ #((get (:active s) 0)) left)) (sorted BAG-ORDER))))))

;; ------------------------------------------------------------ P7 gravity cadence

(defn [(given seeds (st.integers 0 40))] test-p07-gravity-cadence [seed level]
  (setv s (put (booted seed) :level level :acc 0.0)
        n (get CADENCE (get GRAVITY-DEN (min level 29)))
        drawn (:drawn s)
        row (get (:active s) 2)
        since 0
        drops 0)
  (while (< drops 4)
    (setv s (step s)
          since (+ since 1))
    (when (!= (:drawn s) drawn)
      (break))                            ; landed and locked
    (when (!= (get (:active s) 2) row)
      (assert (= (get (:active s) 2) (+ row 1)))
      (assert (= since n))
      (setv row (get (:active s) 2)
            since 0
            drops (+ drops 1))))
  (assert (>= drops 1)))

;; ------------------------------------------------------------ P8 hard drop lands on the ghost

(defn [(given seeds (placed))] test-p08-hard-drop-lands-on-the-ghost [seed bp]
  (setv [board piece] bp
        shape (get piece 0)
        s (put (booted seed) :board board :active piece :dcd 0)
        shown (render-codes s)
        target (cells (landed board piece)))
  (for [[r c] target]
    (when (<= 1 r 17)
      (assert (in (get shown (- r 1) (- c 1)) #("G" shape)))))
  (setv nxt (step s [#("hard_drop" True)])
        clearing (= (phase nxt) "clearing")
        before-clear (if clearing (:flash nxt) (:board nxt)))
  (for [[r c] target]
    (assert (in (get before-clear r c) (if clearing #(shape "W") #(shape))))))

;; ------------------------------------------------------------ P9 ghost geometry

(defn [(given seeds (schedules))] test-p09-ghost-geometry [seed sch]
  (setv [n sched bot] sch)
  (for [[_ _ s] (trajectory seed n sched bot)]
    (when (= (phase s) "playing")
      (setv codes (render-codes s)
            gray (sfor r (range 17) c (range 9) :if (= (get codes r c) "G") #(r c))
            active (sfor [r c] (cells (:active s)) #((- r 1) (- c 1)))
            ghost (sfor [r c] (cells (landed (:board s) (:active s))) #((- r 1) (- c 1))))
      (assert (= gray (sfor [r c] (- ghost active) :if (<= 0 r 16) #(r c)))))))

;; ------------------------------------------------------------ P10 shift inverse

(defn [(given seeds (placed) (st.sampled-from [#("left" "right") #("right" "left")]))]
  test-p10-shift-inverse [seed bp pair]
  (setv [board piece] bp
        [there back] pair
        s (put (booted seed) :board board :active piece)
        s1 (apply-event s there True))
  (when (!= (:active s1) (:active s))
    (assert (= (:active (apply-event s1 back True)) (:active s)))))

;; ------------------------------------------------------------ P11 one gated action per logical frame

(setv gated-events
  (st.lists (st.tuples (st.sampled-from (+ ROTATIONS #("hard_drop" "left" "right" "soft_drop")))
                       (st.booleans))
            :max-size 12)
  latch-sets (st.frozensets (st.sampled-from #("cw" "ccw" "r180" "hard"))))

(defn [(given seeds (placed) (st.integers 0 4) gated-events latch-sets)]
  test-p11-one-gated-action-per-logical-frame [seed bp extra evs spent]
  (setv [board piece] bp
        s (put (booted seed) :board board :active piece :dcd (+ 2 extra) :spent spent)
        successes 0)
  (for [[action down] evs]
    (setv before s
          s (apply-event s action down))
    (when (and down (in action ROTATIONS) (= (:drawn s) (:drawn before))
               (!= (get (:active s) 1) (get (:active before) 1)))
      (+= successes 1))
    (when (and down (= action "hard_drop") (!= (:drawn s) (:drawn before)))
      (+= successes 1))
    (when (game-over-started? s)
      (break))
    (setv s (put s :script #())))         ; after a flash, the same logical frame resumes
  (assert (<= successes 1)))

;; ------------------------------------------------------------ P12 latches

(defn [(given seeds (placed) (st.sampled-from (+ ROTATIONS #("hard_drop"))))]
  test-p12-a-second-press-without-release-does-nothing [seed bp action]
  (setv [board piece] bp
        s (put (booted seed) :board board :active piece :dcd 10)
        s1 (apply-event s action True))
  (assume (not (:script s1)))
  (assert (= (apply-event s1 action True) s1)))

;; ------------------------------------------------------------ P13 scoring

(defn [(given seeds (placed :almost-full True) (st.integers 0 40))]
  test-p13-scoring-formula [seed bp lines]
  (setv [board piece] bp
        ;; level 40: the target (45) exceeds lines, so no level-up this frame;
        ;; a very negative acc keeps gravity out of the frame as well
        s (put (booted seed) :board board :active piece :dcd 0 :lines lines
               :level 40 :acc -100.0)
        nxt (step s [#("hard_drop" True)])
        cleared (if (= (phase nxt) "clearing")
                    (sum (gfor r (range 1 18) :if (= (get (:flash nxt) r) WALL) 1))
                    0))
  (assert (= (- (:score nxt) (:score s))
             (sum (gfor k (range 1 (+ cleared 1)) (* 100 (+ (// (+ lines k) 10) 1))))))
  (assert (= (:lines nxt) (+ lines cleared))))

(defn [(given seeds (schedules))] test-p13-score-moves-only-on-clears [seed sch]
  (setv [n sched bot] sch)
  (for [[prev _ s] (trajectory seed n sched bot)]
    (assert (= 0 (% (:score s) 100)))
    (when (not (new-game? prev s))
      (assert (>= (:score s) (:score prev)))
      (when (!= (:score s) (:score prev))
        ;; the flash of that very clear has just started
        (assert (= (phase s) "clearing"))
        (assert (= (:t s) 0))))))

;; ------------------------------------------------------------ P14 levels

(defn [(given seeds (schedules))] test-p14-levels [seed sch]
  (setv [n sched bot] sch)
  (for [[prev _ s] (trajectory seed n sched bot)]
    (assert (>= (:lines s) 0))
    (when (not (new-game? prev s))
      (assert (<= (:level prev) (:level s) (+ (:level prev) 1))))))

(defn [(given seeds (st.integers 0 60) (st.integers 2 5))]
  test-p14-one-level-per-logical-frame [seed level times]
  (setv s (put (booted seed) :level level :lines (* times (+ level 5)))
        nxt (step s))
  (assert (= (:level nxt) (+ level 1)))
  (assert (= (:lines nxt) (* (- times 1) (+ level 5)))))

;; ------------------------------------------------------------ P15 hold

(defn [(given seeds (placed)
              (st.one-of (st.none) (st.tuples (st.sampled-from BAG-ORDER) (st.integers 0 3))))]
  test-p15-hold [seed bp held]
  (setv [board piece] bp
        s (put (booted seed) :board board :active piece :hold held :hold-ok True)
        s1 (apply-event s "hold" True))
  (assert (= (:hold s1) #((get piece 0) (get piece 1))))
  (if held
      (assert (= (:active s1) #(#* held 0 3)))          ; rotation kept (QUIRK-6)
      (assert (= (:active s1) #((get (:bag s) 0) 0 0 3))))
  (assert (not (:hold-ok s1)))
  (assert (= (apply-event s1 "hold" True) s1)))

(defn [(given seeds (schedules))] test-p15-one-hold-per-locked-piece [seed sch]
  (setv [n sched bot] sch)
  (for [[prev _ s] (trajectory seed n sched bot)]
    ;; hold used before and after, and no piece drawn: no lock came between,
    ;; so no second hold may have happened
    (when (and (not (:hold-ok prev)) (not (:hold-ok s)) (= (:drawn s) (:drawn prev)))
      (assert (= (:hold s) (:hold prev))))))

;; ------------------------------------------------------------ P16 animation lengths, P17 reset

(defn top-out [seed]
  "Hard-drop every frame until a game-over sequence starts. Returns the trail."
  (setv s (booted seed)
        trail [s])
  (while (not (game-over-started? s))
    (setv s (step s (tap "hard_drop")))
    (.append trail s)
    (assert (< (len trail) 400)))
  trail)

(defn test-p16-boot-is-90-countdown-frames-then-a-black-one []
  (setv s (init 1)
        frames [])
  (for [_ (range 91)]
    (setv s (step s))
    (.append frames (render-codes s))
    (assert (= (phase s) "countdown")))
  (assert (= (cut frames 0 30) (* [(get GLYPHS "3")] 30)))
  (assert (= (cut frames 30 60) (* [(get GLYPHS "2")] 30)))
  (assert (= (cut frames 60 90) (* [(get GLYPHS "1")] 30)))
  (assert (= (get frames 90) BLACK))
  (assert (= (phase (step s)) "playing")))

(defn [(given seeds)] test-p16-p17-game-over-sequence-and-reset [seed]
  (setv trail (top-out seed)
        s (get trail -1)
        final-score (:score s)
        old-high (:high-score (get trail -2))
        phases [(phase s)])
  (while (!= (get phases -1) "playing")
    (setv s (step s))
    (.append phases (phase s))
    (assert (< (len phases) 1000)))
  (setv flash (.count phases "clearing"))
  (assert (in flash #(0 5)))
  (assert (= phases (+ (* ["clearing"] flash) (* ["gameover"] 218)
                       (* ["countdown"] 90) ["playing"])))
  ;; P17, at the first countdown frame after the game over
  (setv s (get trail -1))
  (while (!= (phase s) "countdown")
    (setv s (step s)))
  (assert (= #((:score s) (:level s) (:lines s) (:hold s)) #(0 0 0 None)))
  (assert (= (:board s) EMPTY-BOARD))
  (assert (= (len (:bag s)) 6))                          ; a fresh bag, first piece drawn
  (assert (= (:high-score s) (max old-high final-score))))

(defn flash-entry [seed]
  "The first frame of a flash with no game over pending, in a bot game."
  (setv s (booted seed)
        bot (make-bot))
  (for [_ (range 2000)]
    (setv s (step s (bot s)))
    (when (and (= (:script s) FLASH) (= (:t s) 0))
      (return s)))
  None)

(defn until-playing [s]
  "Step without input until play resumes; None if a game over came first."
  (while (!= (phase s) "playing")
    (setv s (step s))
    (when (= (phase s) "gameover")
      (return None)))
  s)

(defn [(given seeds events)] test-p16-a-flash-is-5-frames-p18-flash-input-waits [seed xs]
  (setv entry (flash-entry seed))
  (assume (is-not entry None))
  ;; P16: exactly 5 frames show the flash
  (setv shown [(render-codes entry)]
        t entry)
  (while (and (= (phase t) "clearing") (< (len shown) 20))
    (setv t (step t))
    (.append shown (render-codes t)))
  (assert (= (cut shown 0 5) (* [(get shown 0)] 5)))
  (assert (!= (get shown 5) (get shown 0)))
  ;; P18: events delivered during the flash act as if delivered on the
  ;; first fresh logical frame after it, in order
  (setv a (until-playing (step entry xs))
        b (until-playing entry))
  (assume (and (is-not a None) (is-not b None)))
  (assert (= (step a) (step b xs))))

(defn [(given seeds (st.integers 0 330) events)]
  test-p18-input-is-discarded-during-game-over-and-countdown [seed offset xs]
  (setv s (get (top-out seed) -1))
  (for [_ (range offset)]
    (when (= (phase s) "playing")
      (break))
    (setv s (step s)))
  (setv quiet (step s))
  ;; a flash with a game over pending still queues its input (§9.2)
  (assume (and (in (phase s) #("gameover" "countdown"))
               (in (phase quiet) #("gameover" "countdown"))))
  (assert (= (step s xs) quiet)))

(defn [(given seeds events)] test-p18-the-frame-that-resumes-after-a-game-over-queues-input [seed xs]
  "§9.2: the frame on which the countdown after a game over ends resumes
  the suspended frame, and its own events wait for the next logical frame,
  exactly as on the frame a flash ends. (The prose says so for the flash;
  the pseudocode also for this frame. Spec v2 says it for both.)"
  (setv s (get (top-out seed) -1))
  (while (!= (phase (step s)) "playing")      ; s: the last countdown frame
    (setv s (step s)))
  (assert (= (step (step s xs) []) (step (step s []) xs))))

(defn test-p18-boot-input-is-discarded []
  (setv s (init 3))
  (for [_ (range 91)]
    (assert (= (step s (+ (tap "hard_drop") [#("left" True)])) (step s)))
    (setv s (step s))))
