"""A small JSON Schema (draft 2020-12) validator, standard library only.

    from schema import Schemas
    errors = Schemas(DIR).validate("frame-engine", message)   # [] if valid

It implements exactly the keywords the contract schemas in schemas/ use:
$ref (local "#/$defs/..." and "<file>.schema.json#/..."), $defs, type,
const, enum, properties, required, additionalProperties, items (schema or
false), prefixItems, minItems, maxItems, minimum, maximum, minLength,
maxLength, pattern, allOf, anyOf, oneOf, not, if/then/else. An unknown
keyword is an error in the schema, never silently ignored, so a schema
cannot claim a constraint this validator does not check.

Integers follow the protocol (PROTOCOL §1.5), which is stricter than JSON
Schema: a JSON integer only, never a boolean and never 1.0.
"""

import json
import os
import re

KNOWN = {"$schema", "$id", "$ref", "$defs", "title", "description", "type",
         "const", "enum", "properties", "required", "additionalProperties",
         "items", "prefixItems", "minItems", "maxItems", "minimum", "maximum",
         "minLength", "maxLength", "pattern", "allOf", "anyOf", "oneOf", "not",
         "if", "then", "else"}


_SCALARS = (int, str, bool, float, type(None))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _type_ok(v, t):
    return {
        "object": lambda: isinstance(v, dict),
        "array": lambda: isinstance(v, list),
        "string": lambda: isinstance(v, str),
        "integer": lambda: _is_int(v),
        "number": lambda: _is_int(v) or isinstance(v, float),
        "boolean": lambda: isinstance(v, bool),
        "null": lambda: v is None,
    }[t]()


def _equal(a, b):
    """JSON equality: 1 != true, and 1 != 1.0 under the protocol's rule."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


class SchemaError(Exception):
    """The schema itself is broken (unknown keyword, dangling $ref)."""


class Schemas:
    def __init__(self, directory):
        self.dir = directory
        self.docs = {}
        self._refs = {}      # ($ref, document) -> (node, document)
        self._valid = set()  # (schema node id, document, typed value) known valid
        for name in sorted(os.listdir(directory)):
            if name.endswith(".schema.json"):
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    self.docs[name] = json.load(fh)
        self._check_keywords()

    def names(self):
        return [n[: -len(".schema.json")] for n in self.docs]

    def _check_keywords(self):
        def walk(node, where):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k not in KNOWN:
                        raise SchemaError(f"{where}: unsupported keyword {k!r}")
                    if k in ("properties", "$defs"):
                        for pk, pv in v.items():
                            walk(pv, f"{where}/{k}/{pk}")
                    elif k in ("const", "enum", "required", "title",
                               "description", "$schema", "$id", "$ref",
                               "type", "pattern"):
                        continue
                    else:
                        walk(v, f"{where}/{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{where}[{i}]")
        for name, doc in self.docs.items():
            walk(doc, name)

    def _resolve(self, ref, doc_name):
        hit = self._refs.get((ref, doc_name))
        if hit is None:
            hit = self._refs[(ref, doc_name)] = self._resolve_uncached(ref, doc_name)
        return hit

    def _resolve_uncached(self, ref, doc_name):
        file_part, _, pointer = ref.partition("#")
        name = os.path.basename(file_part) if file_part else doc_name
        if name not in self.docs:
            raise SchemaError(f"{doc_name}: dangling $ref {ref!r}")
        node = self.docs[name]
        for part in [p for p in pointer.split("/") if p]:
            try:
                node = node[part]
            except (KeyError, TypeError):
                raise SchemaError(f"{doc_name}: dangling $ref {ref!r}") from None
        return node, name

    def validate(self, name, value):
        """Findings (strings) for ``value`` against schema ``name``; [] if valid."""
        doc = name if name.endswith(".schema.json") else name + ".schema.json"
        if doc not in self.docs:
            raise SchemaError(f"no schema {name!r}")
        return self._v(self.docs[doc], value, doc, "$")

    def _v(self, s, v, doc, path):
        """_check, memoized for scalars and short lists of scalars (the RGB
        cells and step events that make up most of a frame). Only validity
        is cached, keyed by type as well as value (true is not 1, 1.0 is
        not 1); an invalid value is re-checked to report its path."""
        key = None
        if type(v) in _SCALARS:
            key = (id(s), doc, type(v), v)
        elif type(v) is list and len(v) <= 4 and all(type(x) in _SCALARS for x in v):
            key = (id(s), doc, tuple((type(x), x) for x in v))
        if key is not None and key in self._valid:
            return []
        out = self._check(s, v, doc, path)
        if key is not None and not out:
            self._valid.add(key)
        return out

    def _check(self, s, v, doc, path):
        if s is True:
            return []
        if s is False:
            return [f"{path}: not allowed"]
        out = []
        if "$ref" in s:
            target, tdoc = self._resolve(s["$ref"], doc)
            out += self._v(target, v, tdoc, path)
        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_type_ok(v, t) for t in types):
                return out + [f"{path}: expected {'/'.join(types)}, got {json.dumps(v)[:40]}"]
        if "const" in s and not _equal(v, s["const"]):
            out.append(f"{path}: must be {json.dumps(s['const'])}")
        if "enum" in s and not any(_equal(v, e) for e in s["enum"]):
            out.append(f"{path}: {json.dumps(v)[:40]} is not one of {s['enum']}")
        if isinstance(v, dict):
            for key in s.get("required", ()):
                if key not in v:
                    out.append(f"{path}: missing required field {key!r}")
            props = s.get("properties", {})
            for key, sub in props.items():
                if key in v:
                    out += self._v(sub, v[key], doc, f"{path}.{key}")
            extra = s.get("additionalProperties", True)
            if extra is not True:
                for key in v:
                    if key not in props:
                        out += self._v(extra, v[key], doc, f"{path}.{key}")
        if isinstance(v, list):
            prefix = s.get("prefixItems", [])
            for i, sub in enumerate(prefix[: len(v)]):
                out += self._v(sub, v[i], doc, f"{path}[{i}]")
            if "items" in s:
                for i in range(len(prefix), len(v)):
                    out += self._v(s["items"], v[i], doc, f"{path}[{i}]")
                    if len(out) > 8:
                        break
            if "minItems" in s and len(v) < s["minItems"]:
                out.append(f"{path}: fewer than {s['minItems']} items")
            if "maxItems" in s and len(v) > s["maxItems"]:
                out.append(f"{path}: more than {s['maxItems']} items")
        if isinstance(v, str):
            if "minLength" in s and len(v) < s["minLength"]:
                out.append(f"{path}: shorter than {s['minLength']}")
            if "maxLength" in s and len(v) > s["maxLength"]:
                out.append(f"{path}: longer than {s['maxLength']}")
            if "pattern" in s and not re.search(s["pattern"], v):
                out.append(f"{path}: does not match {s['pattern']}")
        if _is_int(v) or isinstance(v, float):
            if "minimum" in s and v < s["minimum"]:
                out.append(f"{path}: below {s['minimum']}")
            if "maximum" in s and v > s["maximum"]:
                out.append(f"{path}: above {s['maximum']}")
        for sub in s.get("allOf", ()):
            out += self._v(sub, v, doc, path)
        if "anyOf" in s and not any(not self._v(sub, v, doc, path) for sub in s["anyOf"]):
            out.append(f"{path}: matches none of anyOf")
        if "oneOf" in s and sum(not self._v(sub, v, doc, path) for sub in s["oneOf"]) != 1:
            out.append(f"{path}: must match exactly one of oneOf")
        if "not" in s and not self._v(s["not"], v, doc, path):
            out.append(f"{path}: matches a forbidden schema")
        if "if" in s:
            branch = "then" if not self._v(s["if"], v, doc, path) else "else"
            if branch in s:
                out += self._v(s[branch], v, doc, path)
        return out
