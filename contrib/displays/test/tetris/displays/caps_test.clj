(ns tetris.displays.caps-test
  "The EDN tables equal the pinned capabilities.json, and the JSON is the
  pinned one."
  (:require [cheshire.core :as json]
            [clojure.java.io :as io]
            [clojure.spec.alpha :as s]
            [clojure.test :refer [deftest is testing]]
            [tetris.displays.caps :as caps]
            [tetris.displays.specs :as ds])
  (:import [java.io ByteArrayOutputStream]
           [java.security MessageDigest]))

(defn- resource-bytes [path]
  (with-open [in (io/input-stream (io/resource path))]
    (let [out (ByteArrayOutputStream.)]
      (io/copy in out)
      (.toByteArray out))))

(defn- sha256 [^bytes bs]
  (apply str (map #(format "%02x" (bit-and % 0xff))
                  (.digest (MessageDigest/getInstance "SHA-256") bs))))

(def json-caps (delay (json/parse-string (slurp (io/resource caps/json-resource)) true)))

(deftest json-is-the-pinned-one
  (is (= caps/json-sha256 (sha256 (resource-bytes caps/json-resource)))
      "resources/tetris/displays/capabilities.json is not the pinned v0.2.1 file"))

(deftest edn-equals-json
  (testing "the committed EDN is exactly the JSON's edn-keys (run `bb caps:edn` if not)"
    (is (= (select-keys @json-caps caps/edn-keys) caps/caps))
    (is (= (select-keys @json-caps caps/edn-keys)
           (read-string (slurp (io/resource caps/edn-resource)))))))

(deftest the-tables
  (is (= "0.2.1" (:spec caps/caps) caps/spec-version))
  (is (= "cga40" caps/spec-default) "the spec's advertised default")
  (is (= "green-building" caps/default-display) "this repo's default")
  (is (= ["arcade" "blinkenlights" "c64" "cga40" "dc32" "gameboy" "green-building"
          "hub75" "remote" "tetris" "trs80" "ws2812"]
         caps/display-names))
  (is (= ["c64" "cga" "gb" "grey8" "mono" "pico8"] caps/palette-names))
  (is (= {:w 256 :h 256 :cells 65536 :fps 60 :ttl 900 :frameBytes 65538} (:max caps/caps)))
  (is (= ["pal16" "hex"] (:formats caps/caps)))
  (testing "green-building, the repo default"
    (is (= {:w 9 :h 17 :aspect 1.5 :gap 0.35 :palette "cga" :fps 30 :levels 16 :kind "facade"}
           (select-keys (caps/preset "green-building") [:w :h :aspect :gap :palette :fps :levels :kind]))))
  (testing "cga40, the spec default"
    (is (= {:w 40 :h 25 :aspect 1.2 :palette "cga" :fps 30}
           (select-keys (caps/preset :cga40) [:w :h :aspect :palette :fps]))))
  (testing "palette sizes"
    (is (= {"cga" 16 "c64" 16 "pico8" 16 "gb" 4 "grey8" 8 "mono" 2}
           (into {} (map (fn [p] [p (count (caps/palette p))])) caps/palette-names))))
  (testing "every preset is a ::preset, advertises its palette's levels, and is mono iff grey"
    (doseq [d caps/display-names
            :let [p (caps/preset d)]]
      (is (s/valid? ::ds/preset p) (str d " " (s/explain-str ::ds/preset p)))
      (is (= (:levels p) (count (caps/palette (:palette p)))) d)
      (is (= (:mono p) (contains? #{"mono" "grey8"} (:palette p))) d)))
  (is (s/valid? ::ds/caps-edn caps/caps))
  (is (nil? (caps/preset "nope")))
  (is (nil? (caps/preset 42))))
