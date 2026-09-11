"P20 T-legality (SPEC §9.1): the phase machine is a cycle, and the phases
of S0, S1, S2, … form a path of legal edges. Also the other direction:
every legal edge is reachable, so the table is not looser than the engine.
The two edges out of a countdown into a lock (clearing, gameover) are
never reached by play; the witnesses construct them."

(import glob json os)
(import hypothesis [given])
(import tetris_hy.engine [init step phase put apply-event])
(import tetris_hy.conformance [run-trace])
(import tetris_hy.sim.bot [tap])
(import hy_strategies [seeds schedules trajectory booted])

(setv LEGAL #{#("countdown" "countdown") #("countdown" "playing")
              #("countdown" "clearing") #("countdown" "gameover")
              #("playing" "playing") #("playing" "clearing") #("playing" "gameover")
              #("clearing" "clearing") #("clearing" "playing") #("clearing" "gameover")
              #("gameover" "gameover") #("gameover" "countdown")}
      PHASES #("countdown" "playing" "clearing" "gameover")
      ROOT (os.path.abspath (os.path.join (os.path.dirname __file__) ".." ".." ".."))
      TRACES (sorted (glob.glob (os.path.join ROOT "spec" "conformance" "traces" "*.json"))))

(defn path-edges [phases]
  "The edges of the path S0 (a countdown, §9.1), then the given phases."
  (setv path (+ ["countdown"] (list phases)))
  (sfor [a b] (zip path (cut path 1 None)) #(a b)))

(defn test-the-table-is-12-of-the-16-pairs []
  (assert (= (len LEGAL) 12))
  (assert (<= LEGAL (sfor a PHASES b PHASES #(a b))))
  (assert (= (- (sfor a PHASES b PHASES #(a b)) LEGAL)
             #{#("playing" "countdown") #("clearing" "countdown")
               #("gameover" "playing") #("gameover" "clearing")})))

(defn [(given seeds (schedules))] test-p20-t-legality [seed sch]
  (setv [n sched bot] sch)
  (for [[prev _ s] (trajectory seed n sched bot :boot False)]
    (assert (in #((phase prev) (phase s)) LEGAL) #((phase prev) (phase s)))))

(defn test-p20-t-legality-on-every-trace []
  (for [path TRACES]
    (with [fh (open path)]
      (setv trace (json.load fh)))
    (setv phases (get (run-trace trace) "phases"))
    (assert (= (len phases) (get trace "frames")))
    (assert (<= (path-edges phases) LEGAL) path)))

(defn [(given seeds)] test-p20-a-game-over-goes-round-the-cycle [seed]
  (setv s (booted seed)
        seen #{})
  (while (!= (phase s) "gameover")
    (setv nxt (step s (tap "hard_drop")))
    (.add seen #((phase s) (phase nxt)))
    (setv s nxt))
  (for [_ (range 330)]
    (setv nxt (step s))
    (.add seen #((phase s) (phase nxt)))
    (setv s nxt))
  (assert (<= seen LEGAL))
  (assert (<= #{#("gameover" "gameover") #("gameover" "countdown")
                #("countdown" "countdown") #("countdown" "playing")}
              seen)))

;; ------------------------------------------------------------ witnesses

(defn boot-state [seed]
  "S91: after frames 0–90; the next step is frame 91, a fresh logical frame."
  (setv s (init seed))
  (for [_ (range 91)]
    (setv s (step s)))
  s)

(defn test-p20-countdown-to-gameover-is-reachable []
  "Frame 91's batch: soft drops only, so the pieces stack in the spawn
  columns (which can never fill a row) until a spawn collides."
  (setv s (boot-state 1)
        nxt (step s (* [#("soft_drop" True)] 400)))
  (assert (= #((phase s) (phase nxt)) #("countdown" "gameover"))))

(defn clear-batch [seed [max-pieces 14]]
  "Greedy shifts and soft drops (ungated, rotation 0) until the bottom row
  is full. Returns the batch, or None."
  (setv probe (put (boot-state seed) :script #())
        batch [])
  (for [_ (range max-pieces)]
    (setv best None)
    (for [dcol (range -5 6)]
      (setv p probe
            evs []
            move (if (> dcol 0) "right" "left"))
      (for [_ (range (abs dcol))]
        (.append evs #(move True))
        (setv p (apply-event p move True)))
      (setv drawn (:drawn p))
      (while (and (= (:drawn p) drawn) (< (len evs) 60))
        (.append evs #("soft_drop" True))
        (setv p (apply-event p "soft_drop" True)))
      (when (= (phase p) "clearing")
        (return (+ batch evs)))
      (when (= (phase p) "playing")
        (setv board (:board p)
              score #((sum (gfor ch (cut (get board 17) 1 10) (!= ch ".")))
                      (next (gfor r (range 1 18) :if (!= (cut (get board r) 1 10) ".........") r)
                            18)))
        (when (or (is best None) (> score (get best 0)))
          (setv best #(score evs p)))))
    (when (is best None)
      (return None))
    (setv batch (+ batch (get best 1))
          probe (get best 2)))
  None)

(defn test-p20-countdown-to-clearing-is-reachable []
  "Frame 91's batch fills the bottom row, so the frame that ends the boot
  countdown starts a flash."
  (setv found None)
  (for [seed (range 1 200)]
    (setv batch (clear-batch seed))
    (when batch
      (setv found #(seed batch))
      (break)))
  (assert (is-not found None))
  (setv [seed batch] found
        s (boot-state seed)
        nxt (step s batch))
  (assert (= #((phase s) (phase nxt)) #("countdown" "clearing"))))
