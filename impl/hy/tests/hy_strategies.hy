"Hypothesis strategies and trajectory helpers shared by the Hy tests.

Generated input follows SPEC §11: random seeds, events at random frames,
presses without releases, releases without presses, and all eight
actions. The bot is mixed in to reach clears, level-ups and game overs."

(import hypothesis [assume])
(import hypothesis.strategies :as st)
(import tetris_hy.engine [init step collides? WALL EMPTY-ROW])
(import tetris_hy.tables [ACTIONS BAG-ORDER])
(import tetris_hy.sim.bot [make-bot tap])

(setv seeds (st.integers 0 (- (** 2 32) 1))
      events (st.lists (st.tuples (st.sampled-from ACTIONS) (st.booleans))
                       :min-size 1 :max-size 4))

(defn [st.composite] schedules [draw [max-frames 260]]
  "#(frames {frame events} bot?)"
  (setv n (draw (st.integers 1 max-frames)))
  #(n
    (draw (st.dictionaries (st.integers 0 (- n 1)) events :max-size 40))
    (draw (st.booleans))))

(defn booted [seed]
  "The state after the boot sequence (frames 0–90) and the first logical
  frame (frame 91): playing, with the first piece one gravity step in."
  (setv s (init seed))
  (for [_ (range 92)]
    (setv s (step s)))
  s)

(defn trajectory [seed n sched bot [boot True]]
  "Yield #(prev events next) for n frames: the bot's input, if any, then
  the schedule's."
  (setv s (if boot (booted seed) (init seed))
        b (when bot (make-bot)))
  (for [k (range n)]
    (setv evs (+ (if b (list (b s)) []) (list (.get sched k [])))
          nxt (step s evs))
    (yield #(s evs nxt))
    (setv s nxt)))

(defn [st.composite] boards [draw [almost-full False]]
  "A padded board with a random stack and no full row."
  (setv height (draw (st.integers 0 12))
        rows [])
  (for [_ (range height)]
    (setv color (draw (st.sampled-from BAG-ORDER)))
    (if (and almost-full (draw (st.booleans)))
        (setv hole (draw (st.integers 0 8))
              row (.join "" (gfor c (range 9) (if (= c hole) "." color))))
        (setv mask (draw (st.integers 0 510))       ; never 511: no full rows
              row (.join "" (gfor c (range 9) (if (& (>> mask c) 1) color ".")))))
    (.append rows (+ "W" row "W")))
  (+ #(WALL) (* #(EMPTY-ROW) (- 17 height)) (tuple rows) #(WALL WALL)))

(defn [st.composite] placed [draw [almost-full False]]
  "#(board piece), with the piece free on the board."
  (setv board (draw (boards almost-full))
        piece #((draw (st.sampled-from BAG-ORDER)) (draw (st.integers 0 3))
                (draw (st.integers -1 17)) (draw (st.integers -1 9))))
  (assume (not (collides? board piece)))
  #(board piece))
