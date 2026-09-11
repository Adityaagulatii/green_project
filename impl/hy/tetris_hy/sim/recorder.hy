"The headless Display sink and recorder (SPEC §10.2), and the Animation
interface (§10.3).

An Animation is a map of three functions: \"init\" () → state, \"tick\"
(state events) → state, and \"render\" state → Frame. The game is one; any
other frame producer can take its place on the facade."

(import tetris_hy.engine [init step phase])
(import tetris_hy.frame [render rgb-digest])
(import tetris_hy.tables [ROWS COLS FPS])

(setv BLACK-FRAME (tuple (gfor _ (range ROWS) (* #(#(0 0 0)) COLS))))

(defn clamp [v]
  "§2.2: a channel is converted to an integer and clamped into 0..255."
  (max 0 (min (int v) 255)))

(defn normalize [frame]
  "Any 17×9 frame of (r g b) → a tuple frame with clamped channels."
  (when (or (!= (len frame) ROWS) (any (gfor row frame (!= (len row) COLS))))
    (raise (ValueError "a Frame is 17 rows × 9 columns")))
  (tuple (gfor row frame
               (tuple (gfor [r g b] row #((clamp r) (clamp g) (clamp b)))))))

(defclass Recorder []
  "A Display (§2.3) that keeps every frame it is sent, in order, plus the
  input log as [frame action down] triples, so any run can be saved as a
  trace. Frame k is at time k / fps."

  (defn __init__ [self [fps FPS]]
    (setv self.fps fps
          self.frames []
          self.info []
          self.events []))

  (defn makeframe [self]
    BLACK-FRAME)

  (defn send [self frame [info None]]
    (.append self.frames (normalize frame))
    (.append self.info info))

  (defn __len__ [self]
    (len self.frames))

  (defn times [self]
    (lfor k (range (len self.frames)) (/ k self.fps)))

  (defn digests [self]
    (lfor frame self.frames (rgb-digest frame))))

(defn tetris [seed]
  "The game as an Animation."
  {"init" (fn [] (init seed))  "tick" step  "render" render})

(defn hud [s]
  "Score and level for game states; None for other animations."
  (when (and (isinstance s dict) (in "score" s))
    {"score" (:score s)  "level" (:level s)  "lines" (:lines s)
     "high" (:high-score s)  "phase" (phase s)}))

(defn record [animation frames [controller None] [recorder None]]
  "Run `animation` headless for `frames` ticks, sending every frame to the
  recorder. `controller` maps a state to this frame's events. Returns
  #(recorder final-state)."
  (setv rec (if (is recorder None) (Recorder) recorder)
        s ((get animation "init")))
  (for [k (range frames)]
    (setv events (if controller (list (controller s)) []))
    (.extend rec.events (gfor [action down] events [k action (bool down)]))
    (setv s ((get animation "tick") s events))
    (.send rec ((get animation "render") s) (hud s)))
  #(rec s))
