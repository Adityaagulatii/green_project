"The building model: the 153 lit windows of MIT's Green Building
(Building 54, 21 stories) as the 17×9 display (SPEC §10.4).

The row → floor and column → bay mapping is TBD until the hack on
2026-09-13. Until then this is the spec's PROVISIONAL mapping: display
row r is floor 20 − r (rows 0–16 → floors 20–4), and column c is bay
c + 1, counted left to right as seen by the viewer."

(import tetris_hy.tables [ROWS COLS])

(setv GREEN-BUILDING
  {"name" "MIT Green Building (Building 54)"
   "stories" 21  "rows" ROWS  "cols" COLS  "top_floor" 20
   "provisional" True})

(defn check [building]
  "A building must show exactly 153 windows, on floors inside the tower."
  (setv rows (get building "rows")  cols (get building "cols")
        top (get building "top_floor"))
  (when (!= (* rows cols) 153)
    (raise (ValueError "the facade has 153 windows")))
  (when (not (<= rows top (get building "stories")))
    (raise (ValueError "the lit floors must fit inside the tower")))
  building)

(defn window [row col [building GREEN-BUILDING]]
  "The window that shows display cell (row, col)."
  (check building)
  (when (not (and (<= 0 row (- (get building "rows") 1))
                  (<= 0 col (- (get building "cols") 1))))
    (raise (IndexError #(row col))))
  {"row" row  "col" col
   "floor" (- (get building "top_floor") row)
   "bay" (+ col 1)})

(defn windows [[building GREEN-BUILDING]]
  (lfor row (range (get building "rows"))
        col (range (get building "cols"))
        (window row col building)))

(defn lit-floors [[building GREEN-BUILDING]]
  "The floors of display rows 0, 1, … from the top."
  (lfor row (range (get building "rows")) (- (get building "top_floor") row)))
