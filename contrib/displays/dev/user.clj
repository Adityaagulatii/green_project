(ns user
  "REPL entry: clj -M:dev. Loads the display namespaces and instruments every
  s/fdef'd fn."
  (:require [clojure.spec.test.alpha :as stest]
            [tetris.displays.adapt]
            [tetris.displays.caps]
            [tetris.displays.codec]
            [tetris.displays.core]
            [tetris.displays.lease]
            [tetris.displays.relay]
            [tetris.displays.specs]))

(stest/instrument)
