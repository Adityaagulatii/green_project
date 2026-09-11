(ns tetris.displays.relay-test
  "The bb relay end to end over loopback WebSockets (java.net.http, so the
  same client runs on the JVM and under babashka)."
  (:require [cheshire.core :as json]
            [clojure.edn :as edn]
            [clojure.java.io :as io]
            [clojure.string :as str]
            [clojure.test :refer [deftest is testing]]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.lease :as lease]
            [tetris.displays.relay :as relay])
  (:import [java.io ByteArrayOutputStream File]
           [java.net URI]
           [java.net.http HttpClient HttpRequest HttpResponse$BodyHandlers WebSocket WebSocket$Listener]
           [java.nio ByteBuffer]
           [java.util.concurrent LinkedBlockingQueue TimeUnit]))

(defn- connect
  "A client: {:ws :q}; q gets JSON maps, [:text s], [:binary octets], [:close code]."
  [url]
  (let [q (LinkedBlockingQueue.)
        text (StringBuilder.)
        bin (ByteArrayOutputStream.)
        listener (reify WebSocket$Listener
                   (onOpen [_ ws] (.request ws 1))
                   (onText [_ ws data last?]
                     (.append text data)
                     (when last?
                       (let [s (str text)]
                         (.setLength text 0)
                         (.put q (if (str/starts-with? s "{") (json/parse-string s true) [:text s]))))
                     (.request ws 1)
                     nil)
                   (onBinary [_ ws buf last?]
                     (let [bs (byte-array (.remaining buf))]
                       (.get buf bs)
                       (.write bin bs 0 (alength bs)))
                     (when last?
                       (.put q [:binary (mapv #(bit-and % 0xff) (.toByteArray bin))])
                       (.reset bin))
                     (.request ws 1)
                     nil)
                   (onClose [_ _ code _] (.put q [:close code]) nil)
                   (onError [_ _ e] (.put q [:error (str e)])))]
    {:ws (.join (.buildAsync (.newWebSocketBuilder (HttpClient/newHttpClient)) (URI. url) listener))
     :q q}))

(defn- say [{:keys [ws]} m]
  (.join (.sendText ^WebSocket ws (if (string? m) m (json/generate-string m)) true)))

(defn- push [{:keys [ws]} octets]
  (.join (.sendBinary ^WebSocket ws (ByteBuffer/wrap (byte-array (map unchecked-byte octets))) true)))

(defn- recv
  ([c] (recv c 3000))
  ([{:keys [q]} ms] (.poll ^LinkedBlockingQueue q ms TimeUnit/MILLISECONDS)))

(defn- recv-until
  "Messages up to and including the first that satisfies pred (or nil on timeout)."
  [c pred]
  (loop [acc []]
    (let [m (recv c)]
      (cond (nil? m) nil
            (pred m) (conj acc m)
            :else (recur (conj acc m))))))

(defn- bye [{:keys [ws]}] (.join (.sendClose ^WebSocket ws 1000 "")))

(defn- http-get [url]
  (.body (.send (HttpClient/newHttpClient) (.build (HttpRequest/newBuilder (URI. url))) (HttpResponse$BodyHandlers/ofString))))

(defn- tmp [suffix] (doto (File/createTempFile "relay-test" suffix) (.deleteOnExit)))

(deftest relay-end-to-end
  (let [log (tmp ".edn")
        r (relay/start! {:port 0 :log (str log)})
        url (:url r)]
    (try
      (testing "capabilities.json: the pin, with this relay's default and endpoint"
        (let [c (json/parse-string (http-get (str/replace url #"^ws://(.*)/ws$" "http://$1/capabilities.json")) true)]
          (is (= ["green-building" url "0.2.1"] [(:default c) (-> c :endpoints :ws) (:spec c)]))))
      (let [v (connect url)
            s (connect url)
            s2 (connect url)
            gb (fn [n] (vec (repeat 153 n)))]
        (say v {:op "view"})
        (is (= {:op "caps" :display "green-building" :w 9 :h 17 :fps 30 :format "pal16"
                :palette (codec/palette16 (caps/palette "cga"))}
               (recv v))
            "caps")
        (is (= {:op "lease" :display "green-building" :holder nil :expires nil} (recv v)))
        (say s {:op "reserve" :name "t" :ttl 3})
        (let [g (recv s)]
          (is (= ["granted" "pal16" 16] [(:op g) (:format g) (count (:palette g))])))
        (is (= "t" (:holder (recv v))))
        (push s (codec/encode-pal16 (gb 3) 7))
        (push s (gb 4))
        (is (= {:op "error" :reason "rate"} (recv s)) "a frame right after another")
        (is (= [:binary (gb 3)] (last (recv-until v vector?))) "the prefix is stripped")
        (Thread/sleep 60)
        (say s (codec/encode-hex 9 (gb 5)))
        (is (= [:binary (gb 5)] (last (recv-until v vector?))) "hex in, pal16 out")
        (Thread/sleep 60)
        (push s (gb 16))
        (is (= {:op "error" :reason "bad-format"} (recv s)))
        (push s (vec (repeat 152 0)))
        (is (= {:op "error" :reason "bad-frame-length"} (recv s)))
        (say s "{nope")
        (is (= {:op "error" :reason "bad-format"} (recv s)))
        (say s {:op "frobnicate"})
        (is (= {:op "error" :reason "unknown-op"} (recv s)))
        (say s2 {:op "reserve" :name "u"})
        (is (= ["busy" "t"] ((juxt :op :holder) (recv s2))))
        (push s2 (gb 1))
        (is (= {:op "error" :reason "not-holder"} (recv s2)))
        (testing "expiry: lease null, then the black frame"
          (let [ms (recv-until v #(and (map? %) (= "lease" (:op %)) (nil? (:holder %))))]
            (is (some? ms))
            (is (= [:binary (gb 0)] (recv v)))))
        (is (nil? (recv s 300)) "the holder is not told")
        (run! bye [v s s2]))
      (testing "?view= views on connect"
        (let [c (connect (str url "?view=trs80"))]
          (is (= ["caps" "trs80" 10 12] ((juxt :op :display :w :h) (recv c))))
          (bye c)))
      (Thread/sleep 200)
      (testing "the EDN log replays to the relay's state"
        (let [entries (with-open [rd (io/reader log)] (mapv edn/read-string (line-seq rd)))
              st ((:state r))]
          (is (some #(= :expire (:event %)) entries) "the expiry is in the log")
          (is (= (dissoc st :watermark) (dissoc (:state (lease/fold (lease/config) entries)) :watermark)))))
      (finally ((:stop r))))))

(deftest viewer-cap-and-default
  (let [r (relay/start! {:port 0 :max-viewers 1 :default "cga40"})]
    (try
      (let [a (connect (:url r))
            b (connect (:url r))]
        (say a {:op "view"})
        (is (= ["caps" "cga40"] ((juxt :op :display) (recv a))) "--default cga40")
        (say b {:op "view"})
        (let [[_ code] (last (recv-until b #(and (vector? %) (= :close (first %)))))]
          (is (contains? #{1013 1000} code)
              "over the cap: closed (1013 on the JVM; bb's http-kit only closes with 1000)")
          (println (str "RELAY viewer-cap close code=" code)))
        (bye a))
      (finally ((:stop r))))))

(deftest loopback-only
  (is (thrown? clojure.lang.ExceptionInfo (relay/start! {:host "0.0.0.0" :port 0}))))
