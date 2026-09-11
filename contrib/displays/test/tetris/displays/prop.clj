(ns tetris.displays.prop
  "test.check without defspec: babashka ships no
  clojure.test.check.clojure-test, so (holds name prop) runs quick-check
  inside a deftest, on both runtimes, and reports the shrunk counterexample.
  Every run prints one PROP line, so the report can count properties and
  trials."
  (:require [clojure.spec.test.alpha :as stest]
            [clojure.test :refer [is]]
            [clojure.test.check :as tc]))

(def num-tests
  "Trials per property: env PBT_NUM_TESTS, default 50 (the fast loop)."
  (or (some-> (System/getenv "PBT_NUM_TESTS") parse-long) 50))

(defn holds
  "Assert that property `prop` holds for `n` trials (default `num-tests`)."
  ([pname prop] (holds pname num-tests prop))
  ([pname n prop]
   (let [r (tc/quick-check n prop)]
     (println (str "PROP " pname " trials=" (:num-tests r) " pass=" (:pass? r)
                   (when-not (:pass? r) (str " seed=" (:seed r)))))
     (is (true? (:pass? r))
         (binding [*print-length* 12 *print-level* 6]
           (pr-str pname (select-keys r [:seed :fail :shrunk]))))
     r)))

(def hot
  "fdef'd fns the properties call thousands of times; not instrumented in
  the :once fixture (their specs are exercised by stest/check instead)."
  '#{tetris.displays.core/reduce-event tetris.displays.core/reduce-events
     tetris.displays.lease/step tetris.displays.codec/nearest tetris.displays.codec/level})

(defn instrument-fixture
  "A :once fixture: instrument every fdef'd fn of ours but the hot ones."
  [f]
  (let [syms (->> (stest/instrumentable-syms)
                  (filter #(= "tetris.displays" (subs (namespace %) 0 (min 15 (count (namespace %))))))
                  (remove hot))]
    (stest/instrument syms)
    (try (f) (finally (stest/unstrument syms)))))
