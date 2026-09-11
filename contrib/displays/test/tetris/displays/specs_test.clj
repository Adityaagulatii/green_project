(ns tetris.displays.specs-test
  "stest/check over every pure fdef'd fn, and the data specs (STANDARD 5.5)."
  (:require [clojure.spec.alpha :as s]
            [clojure.spec.test.alpha :as stest]
            [clojure.string :as str]
            [clojure.test :refer [deftest is testing]]
            [tetris.displays.adapt]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec]
            [tetris.displays.core :as core]
            [tetris.displays.lease :as lease]
            [tetris.displays.relay]
            [tetris.displays.specs :as ds]))

(def excluded
  "fdef'd but not generatively checked, and where they are covered instead."
  '{tetris.displays.relay/start! "opens a socket: relay-test"
    tetris.displays.relay/advertisement "reads a resource: relay-test"
    tetris.displays.lease/fold "config is the preset table: lease-test L8"
    tetris.displays.lease/run "config is the preset table: lease-test L1-L9"
    tetris.displays.lease/decode-frame "dc is a display config: lease-test frames"})

(defn- ours? [sym] (str/starts-with? (namespace sym) "tetris.displays"))

(def checked
  (->> (stest/checkable-syms) (filter ours?) (remove #(contains? excluded %)) sort vec))

(deftest generative
  (let [results (stest/check checked {:clojure.spec.test.check/opts {:num-tests 50}})]
    (println (str "STEST checked=" (count results) " excluded=" (count excluded)
                  " failures=" (count (filter :failure results))))
    (is (= (count checked) (count results)))
    (doseq [r results]
      (is (nil? (:failure r))
          (binding [*print-length* 10 *print-level* 5]
            (pr-str (:sym r) (select-keys (stest/abbrev-result r) [:failure :spec])))))))

(deftest exclusions-are-real-fdefs
  (is (every? (set (filter ours? (stest/checkable-syms))) (keys excluded))))

(deftest data-specs
  (doseq [sp [::ds/idx ::ds/cells ::ds/palette ::ds/palette16 ::ds/preset ::ds/spec-frame ::ds/decoded
              ::core/state ::lease/log-entry ::lease/effect]]
    (doseq [[v _] (s/exercise sp 8)]
      (is (s/valid? sp v) (str sp))))
  (testing "real values"
    (is (s/valid? ::ds/caps-edn caps/caps))
    (is (s/valid? ::core/state (core/init)))
    (doseq [d caps/display-names]
      (is (s/valid? ::ds/preset (caps/preset d)) d)
      (is (s/valid? ::ds/palette16 (get-in (lease/config) [:displays d :palette])) d))))
