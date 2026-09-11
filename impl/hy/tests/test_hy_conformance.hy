"The Hy engine against the sealed traces, read in place from
spec/conformance/traces. bin/verify.sh runs the same through the driver
process; these tests run it in-process and check the driver's rules."

(import glob importlib.util json os subprocess sys)
(import pytest)
(import tetris_hy.conformance [run-trace make-trace])

(setv ROOT (os.path.abspath (os.path.join (os.path.dirname __file__) ".." ".." ".."))
      TRACES (sorted (glob.glob (os.path.join ROOT "spec" "conformance" "traces" "*.json"))))

(defn load-runner []
  (setv spec (importlib.util.spec-from-file-location
               "conformance_run" (os.path.join ROOT "spec" "conformance" "run.py"))
        runner (importlib.util.module-from-spec spec))
  (.exec-module spec.loader runner)
  runner)

(setv runner (load-runner))

(defn load [path]
  (with [fh (open path)]
    (json.load fh)))

(defn test-the-v1-trace-set-is-there []
  (assert (>= (len TRACES) 14)))

(defn [(pytest.mark.parametrize "path" TRACES :ids os.path.basename)]
  test-hy-engine-passes-trace [path]
  (setv trace (load path))
  (assert (= (runner.validate trace path) []))
  (assert (= (runner.compare trace (run-trace trace)) [])))

(defn test-the-driver-never-reads-the-answers []
  (setv trace (load (get TRACES 1))
        blind (| trace {"digests" [] "final" {}}))
  (assert (= (run-trace blind) (run-trace trace))))

(defn test-the-runner-passes-the-driver-process []
  (setv hy-path (os.path.join ROOT "impl" "hy")
        cmd (.format "env PYTHONPATH={} {} -m hy -m tetris_hy.conformance" hy-path sys.executable))
  (assert (= (runner.main ["--impl" cmd]) 0)))

(defn test-make-trace-round-trips [tmp-path]
  (setv trace (make-trace "x" "a saved run" 3 25 [[0 "left" True]] :digest-every 10)
        path (/ tmp-path "x.json"))
  (assert (= (len (get trace "digests")) 4))                  ; frames 0, 10, 20 and 24
  (with [fh (open path "w")]
    (json.dump trace fh))
  (assert (= (runner.validate (load path) (str path)) []))
  (assert (= (runner.compare trace (run-trace trace)) []))
  (assert (= (len (get (make-trace "x" "" 3 21 [] :digest-every 10) "digests"))
             3)))                                             ; frame 20 is the last
