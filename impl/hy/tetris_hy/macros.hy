"Macros shared by the Hy engine and simulator.

Hy 1.x has no threading macro without hyrule, and the jail installs no
third-party Hy libraries, so the one we use is defined here."

(defmacro -> [head #* forms]
  "Thread `head` through `forms` as their first argument:
  (-> s (f a) g) is (g (f s a))."
  (setv acc head)
  (for [form forms]
    (setv acc (if (isinstance form hy.models.Expression)
                  `(~(get form 0) ~acc ~@(cut form 1 None))
                  `(~form ~acc))))
  acc)
