"17×9 Tetris for the MIT Green Building facade, re-told in Hy.

    (import tetris_hy [init step render])
    (setv s (init 42))
    (for [k (range 200)]
      (setv s (step s (if (= k 100) [#(\"left\" True)] []))))
    (render s)    ; 17 rows × 9 columns of #(r g b)

The engine (engine.hy, frame.hy, tables.hy, prng.hy) is pure. The
simulator is the separate subpackage tetris_hy.sim."

(import tetris_hy.engine [init step phase put])
(import tetris_hy.frame [render render-codes digest])

(setv SPEC-VERSION 2)   ; re-tells Spec v1; sealed as Spec v2 (clarifications only)

(setv __all__ ["init" "step" "phase" "put" "render" "render_codes" "digest"
               "SPEC_VERSION"])
