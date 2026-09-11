"A small deterministic auto-player, for demos and for test coverage.

For each new piece it tries every placement the piece can reach (a
rotation through the engine's own kick rules, then shifts), drops each on
the board and scores the result with the classic four features: aggregate
height, rows cleared, holes and bumpiness. It then plays the chosen
placement as ordinary input events, so its games are ordinary traces."

(import tetris_hy.engine [phase rotate moved collides? landed cells])
(import tetris_hy.tables [ROWS COLS KICKS KICKS-180])

(setv WEIGHTS {"height" -0.51  "lines" 0.76  "holes" -0.36  "bump" -0.18
               "top" -2.0}
      TURNS #(#(1 KICKS "rotate_cw") #(2 KICKS-180 "rotate_180")
              #(3 KICKS "rotate_ccw"))
      TURN-ACTION (dfor [turn _ action] TURNS turn action))

(defn tap [action]
  [#(action True) #(action False)])

(defn placements [s]
  "{#(rot col) piece} for every placement reachable from the active piece."
  (setv board (:board s)
        active (:active s)
        starts [active]
        found {})
  (for [[turn source _] TURNS]
    (setv turned (:active (rotate s turn source)))
    (when (!= (get turned 1) (get active 1))
      (.append starts turned)))
  (for [start starts  dcol #(-1 1)]
    (setv piece start)
    (while True
      (setv (get found #((get piece 1) (get piece 3))) piece
            nxt (moved piece 0 dcol))
      (when (collides? board nxt) (break))
      (setv piece nxt)))
  found)

(defn settle [board piece]
  "The 17×9 grid after the piece drops and full rows clear, and the number
  of rows cleared."
  (setv grid (lfor row (cut board 1 (+ ROWS 1)) (list (cut row 1 (+ COLS 1)))))
  (for [[r c] (cells (landed board piece))]
    (when (and (<= 1 r ROWS) (<= 1 c COLS))
      (setv (get grid (- r 1) (- c 1)) "#")))
  (setv kept (lfor row grid :if (in "." row) row)
        cleared (- ROWS (len kept)))
  #((+ (lfor _ (range cleared) (* ["."] COLS)) kept) cleared))

(defn evaluate [grid cleared [weights WEIGHTS]]
  (setv heights (lfor c (range COLS)
                      (- ROWS (next (gfor r (range ROWS) :if (!= (get grid r c) ".") r)
                                    ROWS)))
        holes (sum (gfor c (range COLS)
                         r (range (- ROWS (get heights c)) ROWS)
                         :if (= (get grid r c) ".")
                         1))
        bump (sum (gfor [a b] (zip heights (cut heights 1 None)) (abs (- a b)))))
  (+ (* (get weights "height") (sum heights))
     (* (get weights "lines") cleared)
     (* (get weights "holes") holes)
     (* (get weights "bump") bump)
     (* (get weights "top") (max 0 (- (max heights) 11)))))

(defn plan [s [weights WEIGHTS]]
  "The best #(rot col) for the active piece, or None."
  (setv best None  best-score None)
  (for [[key piece] (.items (placements s))]
    (setv [grid cleared] (settle (:board s) piece)
          score (evaluate grid cleared weights))
    (when (or (is best-score None) (> score best-score))
      (setv best key  best-score score)))
  best)

(defn make-bot [[pace 1] [think 0] [batch True] [weights WEIGHTS] [patience 12]]
  "A controller: (bot state) → this frame's events.

  pace: act at most every `pace` frames. think: idle frames after a new
  piece appears. batch: do all the shifts and the drop in one frame.
  patience: after this many actions on one piece, just drop it."
  (setv memo {"drawn" None  "target" None  "wait" 0  "actions" 0})
  (defn bot [s]
    (when (!= (phase s) "playing")
      (return []))
    (when (!= (:drawn s) (get memo "drawn"))
      (setv (get memo "drawn") (:drawn s)
            (get memo "target") (plan s weights)
            (get memo "wait") think
            (get memo "actions") 0))
    (when (> (get memo "wait") 0)
      (-= (get memo "wait") 1)
      (return []))
    (setv (get memo "wait") (- pace 1))
    (+= (get memo "actions") 1)
    (setv target (get memo "target")
          [_ rot _ col] (:active s))
    (cond
      (or (is target None) (> (get memo "actions") patience))
        (tap "hard_drop")
      (!= rot (get target 0))
        (tap (get TURN-ACTION (% (- (get target 0) rot) 4)))
      True
        (let [d (- (get target 1) col)
              move (if (> d 0) "right" "left")]
          (if (and d (not batch))
              (tap move)
              (+ (* [#(move True)] (abs d)) (tap "hard_drop"))))))
  bot)
