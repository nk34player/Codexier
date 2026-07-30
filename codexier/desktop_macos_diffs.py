from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import textwrap
from typing import Any


__all__ = [
    "PatchError",
    "PatchSkipped",
    "PATCH_MARKER",
    "PATCH_MARKER_PREFIX",
    "ASAR_PACKAGE",
    "CENTRAL_DIFF",
    "PICKER_DIFF",
    "CENTRAL_DIFF_26721",
    "PICKER_DIFF_26721",
    "CENTRAL_V7_JAVASCRIPT",
    "PICKER_V7_JAVASCRIPT",
    "CODEX_26721_4979_LAYOUT",
    "CODEX_26721_4979_CENTRAL_ANCHOR",
    "CODEX_26721_4979_REQUEST_ANCHOR",
    "CODEX_26721_4979_PREWARM_ANCHOR",
    "CODEX_26721_4979_PICKER_ANCHOR",
    "CODEX_26721_4979_MODELS_ANCHOR",
    "CODEX_26721_4979_MENU_ANCHOR",
    "CODEX_26721_4979_MODEL_CHANGED_ANCHOR",
    "CODEX_26721_5848_MODEL_LABEL_ANCHOR",
    "CODEX_26721_5848_COMPOSER_LABEL_ANCHOR",
    "CODEX_26721_5848_SUBMENU_ANCHOR",
    "CODEX_26721_4979_REACT_ANCHOR",
    "CODEX_26721_5848_LAYOUT",
    "CENTRAL_DIFF_26721_V7",
    "PICKER_DIFF_26721_V7",
    "PATCH_VARIANTS",
    "parse_hunks",
    "render_unified_diff",
    "apply_supported_patch_variant",
    "content_has_current_patch",
    "content_has_any_patch",
]


class PatchError(RuntimeError):
    """A safe, expected patch failure."""


class PatchSkipped(PatchError):
    """A safe patch skip that leaves the desktop application untouched."""


# Single unversioned marker. Edit CENTRAL_V7_JAVASCRIPT / PICKER_V7_JAVASCRIPT only.
# Re-apply always: restore stock from immutable backup, then inject this payload.
PATCH_MARKER = b"__codexDesktopModelProvidersPatch"
PATCH_MARKER_PREFIX = b"__codexDesktopModelProvidersPatch"
ASAR_PACKAGE = "@electron/asar@3.2.10"

# A short content tag embedded right after the marker. When the JS payloads
# change, this tag changes too. patch_status compares the tag found in the
# installed app.asar against the expected tag: a mismatch means the installed
# patch is stale and the user should be prompted to re-apply.
_PATCH_CONTENT_TAG_CACHE: bytes | None = None


def patch_content_tag() -> bytes:
    """Return the content tag for the current JS payloads (computed lazily)."""
    global _PATCH_CONTENT_TAG_CACHE
    if _PATCH_CONTENT_TAG_CACHE is None:
        import hashlib
        digest = hashlib.sha256(
            (CENTRAL_V7_JAVASCRIPT + "\n" + PICKER_V7_JAVASCRIPT).encode("utf-8")
        ).hexdigest()[:12]
        _PATCH_CONTENT_TAG_CACHE = b"v" + digest.encode("ascii")
    return _PATCH_CONTENT_TAG_CACHE


def content_has_current_patch(content: bytes) -> bool:
    """True when the unversioned marker with the current content tag is present."""
    needle = PATCH_MARKER + patch_content_tag()
    return needle in content


def content_has_any_patch(content: bytes) -> bool:
    """True for current or any legacy ...Patch / ...PatchV* install."""
    return PATCH_MARKER_PREFIX in content


def content_has_stale_patch(content: bytes) -> bool:
    """True when a patch marker exists but the content tag differs.

    This detects installs that carry the unversioned marker (or a legacy
    ...PatchV* marker) but NOT the current content tag, meaning the
    JavaScript payloads changed since the app was patched.
    """
    if content_has_current_patch(content):
        return False
    return content_has_any_patch(content)


CENTRAL_DIFF = r"""@@ -4631,6 +4631,146 @@
   if (`data` in e) return e;
   let t = oe(e);
   return t == null ? e : { ...e, data: t };
+}
+function codexProviderRoutingFallback() {
+  return {
+    version: 1,
+    defaultProvider: null,
+    providers: [],
+    modelProviders: {},
+  };
+}
+function codexNormalizeProviderRoutingConfig(e) {
+  if (e == null || typeof e !== `object` || Array.isArray(e))
+    throw Error(`Expected a JSON object`);
+  if (e.version !== 1) throw Error(`Unsupported version`);
+  if (!Array.isArray(e.providers) || e.providers.length === 0)
+    throw Error(`providers must be a non-empty array`);
+  let t = [],
+    n = new Set();
+  for (let r of e.providers) {
+    if (r == null || typeof r !== `object` || Array.isArray(r))
+      throw Error(`Every provider must be an object`);
+    let e = typeof r.id === `string` ? r.id.trim() : ``;
+    if (e.length === 0 || n.has(e))
+      throw Error(`Provider ids must be unique non-empty strings`);
+    n.add(e);
+    let i = typeof r.label === `string` ? r.label.trim() : ``;
+    t.push({
+      id: e,
+      label: i.length > 0 ? i : e,
+      description:
+        typeof r.description === `string` ? r.description.trim() : ``,
+    });
+  }
+  let r =
+    typeof e.default_provider === `string` ? e.default_provider.trim() : ``;
+  if (!n.has(r))
+    throw Error(`default_provider must reference a configured provider`);
+  let i = {};
+  if (
+    e.model_providers == null ||
+    typeof e.model_providers !== `object` ||
+    Array.isArray(e.model_providers)
+  )
+    throw Error(`model_providers must be an object`);
+  for (let [t, r] of Object.entries(e.model_providers)) {
+    let e = t.trim();
+    if (e.length === 0 || typeof r !== `string` || !n.has(r))
+      throw Error(`Every model mapping must reference a configured provider`);
+    i[e] = r;
+  }
+  return {
+    version: 1,
+    defaultProvider: r,
+    providers: t,
+    modelProviders: i,
+  };
+}
+function codexProviderRoutingState() {
+  return (window.__codexDesktopModelProvidersPatchV3 ??= {
+    config: codexProviderRoutingFallback(),
+    configPath: null,
+    error: null,
+    loaded: !1,
+    promise: null,
+  });
+}
+async function codexLoadProviderRoutingConfig(e = !1) {
+  let t = codexProviderRoutingState();
+  if (!e && t.loaded) return t.config;
+  if (t.promise != null) return t.promise;
+  return (
+    (t.promise = (async () => {
+      try {
+        let { codexHome: e } = await Xe(`codex-home`, {
+            params: { hostId: `local` },
+          }),
+          n = e.includes(`\\`) && !e.includes(`/`) ? `\\` : `/`,
+          r = `${e.replace(/[\\/]+$/u, ``)}${n}desktop-model-providers.json`,
+          { contents: i } = await Xe(`read-file`, {
+            params: { hostId: `local`, path: r },
+          }),
+          a = codexNormalizeProviderRoutingConfig(JSON.parse(i));
+        return (
+          (t.config = a),
+          (t.configPath = r),
+          (t.error = null),
+          (t.loaded = !0),
+          a
+        );
+      } catch (e) {
+        return (
+          (t.config = codexProviderRoutingFallback()),
+          (t.error = e instanceof Error ? e.message : String(e)),
+          (t.loaded = !0),
+          t.config
+        );
+      } finally {
+        t.promise = null;
+      }
+    })()),
+    t.promise
+  );
+}
+function codexCustomProviderChoice(e) {
+  try {
+    let t = window.localStorage.getItem(`codex.customProviderSelection.v1`);
+    return t === `auto` || e.providers.some((e) => e.id === t) ? t : `auto`;
+  } catch {
+    return `auto`;
+  }
+}
+async function codexProviderForThreadStart(e) {
+  let t = await codexLoadProviderRoutingConfig(!0),
+    n = codexCustomProviderChoice(t);
+  return n === `auto` ? (t.modelProviders[e?.model] ?? t.defaultProvider) : n;
+}
+async function codexPatchAppServerParams(e, t) {
+  if (e === `thread/start` && t != null && typeof t === `object` && t.modelProvider == null) {
+    let n = await codexProviderForThreadStart(t);
+    return n == null ? t : { ...t, modelProvider: n };
+  }
+  return t;
 }
 var jf,
   Mf,
@@ -4800,6 +4940,7 @@
             throw Error(
               `AppServerRequestClient is missing a message dispatcher`,
             );
+          t = await codexPatchAppServerParams(e, t);
           return e === `config/read`
             ? this.sendConfigReadRequest(t, n)
             : this.enqueueRequest(e, t, n);
@@ -4809,6 +4950,7 @@
             throw Error(
               `AppServerRequestClient is missing a message dispatcher`,
             );
+          e = await codexPatchAppServerParams(`thread/start`, e);
           return this.enqueueRequest(
             `thread/start`,
             e,
"""


PICKER_DIFF = r"""@@ -10162,6 +10162,204 @@
       };
 }
 var jO = e(() => {});
+function codexPickerProviderRoutingFallback() {
+  return {
+    version: 1,
+    defaultProvider: null,
+    providers: [],
+    modelProviders: {},
+  };
+}
+function codexPickerNormalizeProviderRoutingConfig(e) {
+  if (e == null || typeof e !== `object` || Array.isArray(e))
+    throw Error(`Expected a JSON object`);
+  if (e.version !== 1) throw Error(`Unsupported version`);
+  if (!Array.isArray(e.providers) || e.providers.length === 0)
+    throw Error(`providers must be a non-empty array`);
+  let t = [],
+    n = new Set();
+  for (let r of e.providers) {
+    if (r == null || typeof r !== `object` || Array.isArray(r))
+      throw Error(`Every provider must be an object`);
+    let e = typeof r.id === `string` ? r.id.trim() : ``;
+    if (e.length === 0 || n.has(e))
+      throw Error(`Provider ids must be unique non-empty strings`);
+    n.add(e);
+    let i = typeof r.label === `string` ? r.label.trim() : ``;
+    t.push({
+      id: e,
+      label: i.length > 0 ? i : e,
+      description:
+        typeof r.description === `string` ? r.description.trim() : ``,
+    });
+  }
+  let r =
+    typeof e.default_provider === `string` ? e.default_provider.trim() : ``;
+  if (!n.has(r))
+    throw Error(`default_provider must reference a configured provider`);
+  let i = {};
+  if (
+    e.model_providers == null ||
+    typeof e.model_providers !== `object` ||
+    Array.isArray(e.model_providers)
+  )
+    throw Error(`model_providers must be an object`);
+  for (let [t, r] of Object.entries(e.model_providers)) {
+    let e = t.trim();
+    if (e.length === 0 || typeof r !== `string` || !n.has(r))
+      throw Error(`Every model mapping must reference a configured provider`);
+    i[e] = r;
+  }
+  return {
+    version: 1,
+    defaultProvider: r,
+    providers: t,
+    modelProviders: i,
+  };
+}
+function codexPickerProviderRoutingState() {
+  return (window.__codexDesktopModelProvidersPatchV3 ??= {
+    config: codexPickerProviderRoutingFallback(),
+    configPath: null,
+    error: null,
+    loaded: !1,
+    promise: null,
+  });
+}
+async function codexPickerLoadProviderRoutingConfig(e = !1) {
+  let t = codexPickerProviderRoutingState();
+  if (!e && t.loaded) return t.config;
+  if (t.promise != null) return t.promise;
+  return (
+    (t.promise = (async () => {
+      try {
+        let { codexHome: e } = await ye(`codex-home`, {
+            params: { hostId: `local` },
+          }),
+          n = e.includes(`\\`) && !e.includes(`/`) ? `\\` : `/`,
+          r = `${e.replace(/[\\/]+$/u, ``)}${n}desktop-model-providers.json`;
+        t.configPath = r;
+        let { contents: i } = await ye(`read-file`, {
+            params: { hostId: `local`, path: r },
+          }),
+          a = codexPickerNormalizeProviderRoutingConfig(JSON.parse(i));
+        return ((t.config = a), (t.error = null), (t.loaded = !0), a);
+      } catch (e) {
+        return (
+          (t.config = codexPickerProviderRoutingFallback()),
+          (t.error = e instanceof Error ? e.message : String(e)),
+          (t.loaded = !0),
+          t.config
+        );
+      } finally {
+        t.promise = null;
+      }
+    })()),
+    t.promise
+  );
+}
+function codexReadCustomProviderChoice(e) {
+  try {
+    let t = window.localStorage.getItem(`codex.customProviderSelection.v1`);
+    return t === `auto` || e.providers.some((e) => e.id === t) ? t : `auto`;
+  } catch {
+    return `auto`;
+  }
+}
+function codexWriteCustomProviderChoice(e) {
+  try {
+    window.localStorage.setItem(`codex.customProviderSelection.v1`, e);
+  } catch {}
+}
+function CodexCustomProviderPickerSection() {
+  let r = codexPickerProviderRoutingState(),
+    [e, t] = CodexProviderPatchReact.useState(r.config),
+    [n, i] = CodexProviderPatchReact.useState(r.error),
+    [a, o] = CodexProviderPatchReact.useState(() =>
+      codexReadCustomProviderChoice(r.config),
+    );
+  CodexProviderPatchReact.useEffect(() => {
+    let e = !0;
+    return (
+      codexPickerLoadProviderRoutingConfig(!0).then((n) => {
+        e &&
+          (t(n),
+          i(codexPickerProviderRoutingState().error),
+          o((e) =>
+            e === `auto` || n.providers.some((t) => t.id === e) ? e : `auto`,
+          ));
+      }),
+      () => {
+        e = !1;
+      }
+    );
+  }, []);
+  let s = (e) => (t) => {
+      (t?.preventDefault(), codexWriteCustomProviderChoice(e), o(e));
+    },
+    c =
+      e.providers.find((t) => t.id === e.defaultProvider)?.label ??
+      e.defaultProvider,
+    l = e.providers.map((e) =>
+      (0, FO.jsx)(
+        zy.Item,
+        {
+          RightIcon: a === e.id ? ct : void 0,
+          SubText:
+            e.description.length === 0
+              ? null
+              : (0, FO.jsx)(`span`, {
+                  className: `text-token-description-foreground`,
+                  children: e.description,
+                }),
+          onSelect: s(e.id),
+          children: e.label,
+        },
+        e.id,
+      ),
+    );
+  return (0, FO.jsxs)(FO.Fragment, {
+    children: [
+      (0, FO.jsx)(zy.Title, { children: `Provider for new tasks` }),
+      n == null
+        ? null
+        : (0, FO.jsx)(zy.Item, {
+            disabled: !0,
+            SubText: (0, FO.jsx)(`span`, {
+              className: `text-token-description-foreground`,
+              children: n,
+            }),
+            children: `Provider config error — using fallback`,
+          }),
+      (0, FO.jsx)(zy.Item, {
+        RightIcon: a === `auto` ? ct : void 0,
+        SubText: (0, FO.jsx)(`span`, {
+          className: `text-token-description-foreground`,
+          children: `Uses the mapped provider for each model; ${c} when unmapped`,
+        }),
+        onSelect: s(`auto`),
+        children: `Automatic`,
+      }),
+      l,
+      (0, FO.jsx)(zy.Separator, {}),
+    ],
+  });
+}
 function MO(e) {
   let t = (0, PO.c)(169),
     {
@@ -10312,6 +10510,7 @@
       ? (s = t[48])
       : ((s = (0, FO.jsxs)(FO.Fragment, {
           children: [
+            (0, FO.jsx)(CodexCustomProviderPickerSection, {}),
             a,
             (0, FO.jsx)(`div`, {
               className: `vertical-scroll-fade-mask flex max-h-[250px] flex-col overflow-y-auto`,
@@ -10984,8 +11183,10 @@
 }
 var PO,
   FO,
+  CodexProviderPatchReact,
   IO = e(() => {
     ((PO = w()),
+      (CodexProviderPatchReact = PO),
       T(),
       Q(),
       Pg(),
"""


# ChatGPT 26.721 moved both targets into app-initial, renamed the minified
# bindings, and introduced the Power Picker. Keep a separate exact-hunk variant
# so unsupported future builds still fail before the installed app is touched.
CENTRAL_DIFF_26721 = (
    CENTRAL_DIFF.replace("   let t = oe(e);", "   let t = abe(e);", 1)
    .replace("await Xe(`codex-home`", "await tp(`codex-home`", 1)
    .replace("await Xe(`read-file`", "await tp(`read-file`", 1)
    .replace(" var jf,\n   Mf,", " var s9t,\n   c9t,", 1)
    .replace(
        """@@ -4809,6 +4950,7 @@
             throw Error(
               `AppServerRequestClient is missing a message dispatcher`,
             );
+          e = await codexPatchAppServerParams(`thread/start`, e);
           return this.enqueueRequest(
             `thread/start`,
             e,
""",
        """@@ -137758,6 +137899,7 @@
             throw Error(
               `AppServerRequestClient is missing a message dispatcher`,
             );
+          e = await codexPatchAppServerParams(`thread/start`, e);
           let n = t?.priority ?? `critical`,
             r = Q7t(`thread/start`, t?.source),
             i =
""",
        1,
    )
)


PICKER_DIFF_26721 = r"""@@ -520849,7 +520849,7 @@
 }
 function Scs(e) {
-  let t = (0, wcs.c)(12),
+  let t = (0, wcs.c)(13),
     { submenu: n } = e,
     r = n.ariaLabel,
     i = n.contentClassName,
@@ -520871,10 +520871,15 @@
     t[7] !== n.label ||
     t[8] !== n.value ||
     t[9] !== o ||
-    t[10] !== l
+    t[10] !== l ||
+    t[11] !== n.extras
       ? ((u = (0, QX.jsx)(Kos, {
           ariaLabel: r,
           contentClassName: i,
           disabled: a,
           flyoutHeader: o,
           label: s,
           value: c,
-          children: l,
+          children:
+            n.extras == null
+              ? l
+              : (0, QX.jsxs)(QX.Fragment, { children: [n.extras, l] }),
         })),
         (t[4] = n.ariaLabel),
         (t[5] = n.contentClassName),
@@ -520887,8 +520892,9 @@
         (t[8] = n.value),
         (t[9] = o),
         (t[10] = l),
-        (t[11] = u))
-      : (u = t[11]),
+        (t[11] = n.extras),
+        (t[12] = u))
+      : (u = t[12]),
     u
   );
 }
@@ -549520,6 +549525,202 @@
       (xMs = Aa(Q, (e, { get: t }) =>
         bMs({
           conversationId: e,
           resumeState: t(PD, e) ?? void 0,
           turnCount: t(LD, e),
         }),
       )));
   });
+function codexPickerProviderRoutingFallback() {
+  return {
+    version: 1,
+    defaultProvider: null,
+    providers: [],
+    modelProviders: {},
+  };
+}
+function codexPickerNormalizeProviderRoutingConfig(e) {
+  if (e == null || typeof e !== `object` || Array.isArray(e))
+    throw Error(`Expected a JSON object`);
+  if (e.version !== 1) throw Error(`Unsupported version`);
+  if (!Array.isArray(e.providers) || e.providers.length === 0)
+    throw Error(`providers must be a non-empty array`);
+  let t = [],
+    n = new Set();
+  for (let r of e.providers) {
+    if (r == null || typeof r !== `object` || Array.isArray(r))
+      throw Error(`Every provider must be an object`);
+    let e = typeof r.id === `string` ? r.id.trim() : ``;
+    if (e.length === 0 || n.has(e))
+      throw Error(`Provider ids must be unique non-empty strings`);
+    n.add(e);
+    let i = typeof r.label === `string` ? r.label.trim() : ``;
+    t.push({
+      id: e,
+      label: i.length > 0 ? i : e,
+      description:
+        typeof r.description === `string` ? r.description.trim() : ``,
+    });
+  }
+  let r =
+    typeof e.default_provider === `string` ? e.default_provider.trim() : ``;
+  if (!n.has(r))
+    throw Error(`default_provider must reference a configured provider`);
+  let i = {};
+  if (
+    e.model_providers == null ||
+    typeof e.model_providers !== `object` ||
+    Array.isArray(e.model_providers)
+  )
+    throw Error(`model_providers must be an object`);
+  for (let [t, r] of Object.entries(e.model_providers)) {
+    let e = t.trim();
+    if (e.length === 0 || typeof r !== `string` || !n.has(r))
+      throw Error(`Every model mapping must reference a configured provider`);
+    i[e] = r;
+  }
+  return {
+    version: 1,
+    defaultProvider: r,
+    providers: t,
+    modelProviders: i,
+  };
+}
+function codexPickerProviderRoutingState() {
+  return (window.__codexDesktopModelProvidersPatchV3 ??= {
+    config: codexPickerProviderRoutingFallback(),
+    configPath: null,
+    error: null,
+    loaded: !1,
+    promise: null,
+  });
+}
+async function codexPickerLoadProviderRoutingConfig(e = !1) {
+  let t = codexPickerProviderRoutingState();
+  if (!e && t.loaded) return t.config;
+  if (t.promise != null) return t.promise;
+  return (
+    (t.promise = (async () => {
+      try {
+        let { codexHome: e } = await tp(`codex-home`, {
+            params: { hostId: `local` },
+          }),
+          n = e.includes(`\\`) && !e.includes(`/`) ? `\\` : `/`,
+          r = `${e.replace(/[\\/]+$/u, ``)}${n}desktop-model-providers.json`;
+        t.configPath = r;
+        let { contents: i } = await tp(`read-file`, {
+            params: { hostId: `local`, path: r },
+          }),
+          a = codexPickerNormalizeProviderRoutingConfig(JSON.parse(i));
+        return ((t.config = a), (t.error = null), (t.loaded = !0), a);
+      } catch (e) {
+        return (
+          (t.config = codexPickerProviderRoutingFallback()),
+          (t.error = e instanceof Error ? e.message : String(e)),
+          (t.loaded = !0),
+          t.config
+        );
+      } finally {
+        t.promise = null;
+      }
+    })()),
+    t.promise
+  );
+}
+function codexReadCustomProviderChoice(e) {
+  try {
+    let t = window.localStorage.getItem(`codex.customProviderSelection.v1`);
+    return t === `auto` || e.providers.some((e) => e.id === t) ? t : `auto`;
+  } catch {
+    return `auto`;
+  }
+}
+function codexWriteCustomProviderChoice(e) {
+  try {
+    window.localStorage.setItem(`codex.customProviderSelection.v1`, e);
+  } catch {}
+}
+function CodexCustomProviderPickerSection() {
+  let r = codexPickerProviderRoutingState(),
+    [e, t] = CodexProviderPatchReact.useState(r.config),
+    [n, i] = CodexProviderPatchReact.useState(r.error),
+    [a, o] = CodexProviderPatchReact.useState(() =>
+      codexReadCustomProviderChoice(r.config),
+    );
+  CodexProviderPatchReact.useEffect(() => {
+    let e = !0;
+    return (
+      codexPickerLoadProviderRoutingConfig(!0).then((n) => {
+        e &&
+          (t(n),
+          i(codexPickerProviderRoutingState().error),
+          o((e) =>
+            e === `auto` || n.providers.some((t) => t.id === e) ? e : `auto`,
+          ));
+      }),
+      () => {
+        e = !1;
+      }
+    );
+  }, []);
+  let s = (e) => (t) => {
+      (t?.preventDefault(), codexWriteCustomProviderChoice(e), o(e));
+      void Rf(`clear-prewarmed-threads-for-host`, { hostId: `local` }).catch(
+        () => {},
+      );
+    },
+    c =
+      e.providers.find((t) => t.id === e.defaultProvider)?.label ??
+      e.defaultProvider,
+    l = e.providers.map((e) =>
+      (0, wQ.jsx)(
+        yz.Item,
+        {
+          RightIcon: a === e.id ? Ym : void 0,
+          SubText:
+            e.description.length === 0
+              ? null
+              : (0, wQ.jsx)(`span`, {
+                  className: `text-token-description-foreground`,
+                  children: e.description,
+                }),
+          onSelect: s(e.id),
+          children: e.label,
+        },
+        e.id,
+      ),
+    );
+  return (0, wQ.jsxs)(wQ.Fragment, {
+    children: [
+      (0, wQ.jsx)(yz.Title, { children: `Provider for new tasks` }),
+      n == null
+        ? null
+        : (0, wQ.jsx)(yz.Item, {
+            disabled: !0,
+            SubText: (0, wQ.jsx)(`span`, {
+              className: `text-token-description-foreground`,
+              children: n,
+            }),
+            children: `Provider config error — using fallback`,
+          }),
+      (0, wQ.jsx)(yz.Item, {
+        RightIcon: a === `auto` ? Ym : void 0,
+        SubText: (0, wQ.jsx)(`span`, {
+          className: `text-token-description-foreground`,
+          children: `Uses the mapped provider for each model; ${c} when unmapped`,
+        }),
+        onSelect: s(`auto`),
+        children: `Automatic`,
+      }),
+      l,
+      (0, wQ.jsx)(yz.Separator, {}),
+    ],
+  });
+}
 function CMs(e) {
   let t = (0, TMs.c)(164),
@@ -549693,6 +549895,7 @@
           value: s,
         },
         model: {
+          extras: (0, wQ.jsx)(CodexCustomProviderPickerSection, {}),
           ariaLabel: U.formatMessage(
             {
               id: `composer.intelligenceDropdown.model.rowAriaLabel`,
@@ -549782,6 +549985,7 @@
       : ((g = (0, wQ.jsxs)(wQ.Fragment, {
           children: [
+            (0, wQ.jsx)(CodexCustomProviderPickerSection, {}),
             m,
             (0, wQ.jsx)(`div`, {
               className: `vertical-scroll-fade-mask flex max-h-[250px] flex-col overflow-y-auto`,
@@ -550438,11 +550642,13 @@
 }
 var TMs,
   wQ,
+  CodexProviderPatchReact,
   EMs = e(() => {
     ((TMs = c()),
+      (CodexProviderPatchReact = TMs),
       pd(),
       ad(),
       gls(),
"""


# Provider-first V7 patch helpers.
def _append_insertion_diff(
    diff: str, context: str, inserted: str
) -> str:
    """Append a source-validated insertion hunk without hand-prefixing lines."""
    return (
        diff
        + "@@ provider-first insertion @@\n"
        + "".join(f"+{line}\n" for line in inserted.splitlines())
        + "".join(f" {line}\n" for line in context.splitlines())
    )


def _replace_added_block(
    diff: str, start: str, end: str, replacement: str
) -> str:
    """Replace one generated JavaScript block while retaining source context."""
    start = f"+{start}\n"
    end = "".join(f" {line}\n" for line in end.splitlines())
    if diff.count(start) != 1 or diff.count(end) != 1:
        raise RuntimeError("Embedded patch block is not unique")
    before, remainder = diff.split(start, 1)
    _, after = remainder.split(end, 1)
    added = "".join(f"+{line}\n" for line in replacement.splitlines())
    return before + added + end + after


def _insert_hunk_before(diff: str, anchor: str, hunk: str) -> str:
    if diff.count(anchor) != 1:
        raise RuntimeError("Embedded patch hunk anchor is not unique")
    return diff.replace(anchor, hunk + anchor)


CENTRAL_V7_JAVASCRIPT = r"""function codexProviderRoutingFallbackV4() {
  return {
    version: 2,
    defaultProvider: null,
    providers: [],
  };
}
function codexNormalizeProviderRoutingConfigV4(e) {
  if (e == null || typeof e !== `object` || Array.isArray(e))
    throw Error(`Expected a JSON object`);
  if (e.version !== 2 || !Array.isArray(e.providers) || e.providers.length === 0)
    throw Error(`Expected provider-routing config version 2`);
  let t = [], n = new Set();
  for (let r of e.providers) {
    if (r == null || typeof r !== `object` || Array.isArray(r))
      throw Error(`Every provider must be an object`);
    let e = typeof r.id === `string` ? r.id.trim() : ``;
    if (e.length === 0 || n.has(e) || !Array.isArray(r.models))
      throw Error(`Providers must have unique ids and model arrays`);
    n.add(e);
    let i = new Set(), a = [];
    for (let t of r.models) {
      let n = typeof t?.id === `string` ? t.id.trim() : ``,
        r = typeof t?.label === `string` ? t.label.trim() : ``;
      if (n.length === 0 || r.length === 0 || i.has(n))
        throw Error(`Provider models must have unique ids and labels`);
      (i.add(n), a.push({ id: n, label: r }));
    }
    let bu = typeof r.base_url === `string` ? r.base_url : ``,
      tk = typeof r.token === `string` ? r.token : ``;
    t.push({
      id: e,
      label: typeof r.label === `string` && r.label.trim().length > 0 ? r.label.trim() : e,
      description: typeof r.description === `string` ? r.description.trim() : ``,
      base_url: bu,
      token: tk,
      models: a,
    });
  }
  let r = typeof e.default_provider === `string` ? e.default_provider.trim() : ``;
  if (!n.has(r)) throw Error(`default_provider must reference a configured provider`);
  return { version: 2, defaultProvider: r, providers: t };
}
function codexProviderRoutingStateV4() {
  return (window.__codexDesktopModelProvidersPatch ??= {
    config: codexProviderRoutingFallbackV4(), error: null, loaded: !1, promise: null,
  });
}
async function codexLoadProviderRoutingConfigV4(e = !1) {
  let t = codexProviderRoutingStateV4();
  if (!e && t.loaded) return t.config;
  if (t.promise != null) return t.promise;
  return (t.promise = (async () => {
    try {
      let { codexHome: e } = await tp(`codex-home`, { params: { hostId: `local` } }),
        n = e.includes(`\\`) && !e.includes(`/`) ? `\\` : `/`,
        r = `${e.replace(/[\\/]+$/u, ``)}${n}desktop-model-providers.json`,
        { contents: i } = await tp(`read-file`, { params: { hostId: `local`, path: r } }),
        a = codexNormalizeProviderRoutingConfigV4(JSON.parse(i));
      return ((t.config = a), (t.error = null), (t.loaded = !0), a);
    } catch (e) {
      return ((t.config = codexProviderRoutingFallbackV4()), (t.error = e instanceof Error ? e.message : String(e)), (t.loaded = !0), t.config);
    } finally {
      t.promise = null;
    }
  })(), t.promise);
}
async function codexPatchAppServerParams(e, t) {
  if (e === `thread/list`) {
    let n = t != null && typeof t === `object` ? t : {};
    return { ...n, modelProviders: null };
  }
  if (e !== `thread/start` || t == null || typeof t !== `object`) return t;
  let n = await codexLoadProviderRoutingConfigV4(!0), r;
  try { r = window.localStorage.getItem(`codex.customProviderSelection.v2`); } catch {}
  let i = n.providers.find((e) => e.id === r) ?? n.providers.find((e) => e.id === n.defaultProvider);
  if (i == null) return t;
  let out = { ...t, modelProvider: `codexier` };
  let hasModel = typeof t.model === `string` && i.models.some((e) => e.id === t.model);
  if (!hasModel && i.models.length > 0) out.model = i.models[0].id;
  return out;
}"""

PICKER_V7_JAVASCRIPT = r"""function codexPickerProviderRoutingFallbackV4() {
  return {
    version: 2,
    defaultProvider: null,
    providers: [],
  };
}
function codexPickerCachedProviderRoutingConfigV4() {
  try {
    let e = JSON.parse(window.localStorage.getItem(`codex.customProviderRouting.v4`));
    if (e != null && e.version === 2 && Array.isArray(e.providers)) return e;
  } catch {}
  return codexPickerProviderRoutingFallbackV4();
}
function codexPickerNormalizeProviderRoutingConfigV4(e) {
  if (e == null || typeof e !== `object` || Array.isArray(e))
    throw Error(`Expected a JSON object`);
  if (e.version !== 2 || !Array.isArray(e.providers) || e.providers.length === 0)
    throw Error(`Expected provider-routing config version 2`);
  let t = [], n = new Set();
  for (let r of e.providers) {
    let e = typeof r?.id === `string` ? r.id.trim() : ``;
    if (e.length === 0 || n.has(e) || !Array.isArray(r?.models))
      throw Error(`Providers must have unique ids and model arrays`);
    n.add(e);
    let i = new Set(), a = [];
    for (let t of r.models) {
      let n = typeof t?.id === `string` ? t.id.trim() : ``,
        r = typeof t?.label === `string` ? t.label.trim() : ``;
      if (n.length === 0 || r.length === 0 || i.has(n))
        throw Error(`Provider models must have unique ids and labels`);
      (i.add(n), a.push({ id: n, label: r }));
    }
    let bu = typeof r.base_url === `string` ? r.base_url : ``,
      tk = typeof r.token === `string` ? r.token : ``;
    t.push({
      id: e,
      label: typeof r.label === `string` && r.label.trim().length > 0 ? r.label.trim() : e,
      description: typeof r.description === `string` ? r.description.trim() : ``,
      base_url: bu,
      token: tk,
      models: a,
    });
  }
  let r = typeof e.default_provider === `string` ? e.default_provider.trim() : ``;
  if (!n.has(r)) throw Error(`default_provider must reference a configured provider`);
  return { version: 2, defaultProvider: r, providers: t };
}
function codexPickerProviderRoutingStateV4() {
  return (window.__codexDesktopModelProvidersPatch ??= {
    config: codexPickerCachedProviderRoutingConfigV4(), error: null, loaded: !1, promise: null,
  });
}
async function codexPickerLoadProviderRoutingConfigV4(e = !1) {
  let t = codexPickerProviderRoutingStateV4();
  if (!e && t.loaded) return t.config;
  if (t.promise != null) return t.promise;
  return (t.promise = (async () => {
    try {
      let { codexHome: e } = await tp(`codex-home`, { params: { hostId: `local` } }),
        n = e.includes(`\\`) && !e.includes(`/`) ? `\\` : `/`,
        r = `${e.replace(/[\\/]+$/u, ``)}${n}desktop-model-providers.json`,
        { contents: i } = await tp(`read-file`, { params: { hostId: `local`, path: r } }),
        a = codexPickerNormalizeProviderRoutingConfigV4(JSON.parse(i));
      try { window.localStorage.setItem(`codex.customProviderRouting.v4`, JSON.stringify(a)); } catch {}
      codexSyncConfigOnLoad().catch(() => {});
      return (
        (t.config = a),
        (t.error = null),
        (t.loaded = !0),
        window.dispatchEvent(new Event(`codex.customProviderRouting.v4.change`)),
        a
      );
    } catch (e) {
      return (
        (t.error = e instanceof Error ? e.message : String(e)),
        (t.loaded = !0),
        window.dispatchEvent(new Event(`codex.customProviderRouting.v4.change`)),
        t.config
      );
    } finally {
      t.promise = null;
    }
  })(), t.promise);
}
function codexReadProviderChoiceV4(e) {
  try {
    let t = window.localStorage.getItem(`codex.customProviderSelection.v2`);
    if (e.providers.some((e) => e.id === t)) return t;
  } catch {}
  return e.defaultProvider;
}
function codexPickerModelLabelV4(e, t) {
  let o = typeof e === `string` ? e : typeof e?.model === `string` ? e.model : typeof e?.id === `string` ? e.id : ``,
    n = codexPickerProviderRoutingStateV4().config,
    r = typeof e?.providerId === `string` ? e.providerId : codexReadProviderChoiceV4(n),
    i = n.providers.find((e) => e.id === r) ?? n.providers.find((e) => e.id === n.defaultProvider),
    s = i?.models.find((e) => e.id === o);
  if (s != null) return `${s.label} (${i.label})`;
  let a = n.providers.filter((e) => e.models.some((e) => e.id === o));
  if (a.length === 1) {
    let e = a[0].models.find((e) => e.id === o);
    return `${e.label} (${a[0].label})`;
  }
  return t;
}
function codexUseProviderModels(e, s, c) {
  let r = codexPickerProviderRoutingStateV4(),
    [t, n] = CodexProviderPatchReact.useState(r.config),
    [i, a] = CodexProviderPatchReact.useState(() => codexReadProviderChoiceV4(r.config));
  CodexProviderPatchReact.useEffect(() => {
    let e = !0;
    return (codexPickerLoadProviderRoutingConfigV4(!0).then((t) => {
      e && (n(t), a(codexReadProviderChoiceV4(t)));
    }), () => { e = !1; });
  }, []);
  CodexProviderPatchReact.useEffect(() => {
    let e = () => a(codexReadProviderChoiceV4(codexPickerProviderRoutingStateV4().config));
    return (window.addEventListener(`codex.customProviderSelection.v2.change`, e), () => {
      window.removeEventListener(`codex.customProviderSelection.v2.change`, e);
    });
  }, []);
  let o = t.providers.find((e) => e.id === i) ?? t.providers.find((e) => e.id === t.defaultProvider);
  let l = o == null || !Array.isArray(e) || e.length === 0 ? e : o.models.map((t) => ({
      ...e[0],
      id: t.id,
      model: t.id,
      providerId: o.id,
      name: t.label,
      label: t.label,
      displayName: `${t.label} (${o.label})`,
    }));
  CodexProviderPatchReact.useEffect(() => {
    if (o != null && s != null && !l.some((e) => e.model === s) && l[0] != null)
      c?.(l[0].model, l[0].defaultReasoningEffort);
  }, [o, s, c, l]);
  return l;
}
function CodexCustomProviderPickerSection() {
  let r = codexPickerProviderRoutingStateV4(),
    [e, t] = CodexProviderPatchReact.useState(r.config),
    [n, i] = CodexProviderPatchReact.useState(() => codexReadProviderChoiceV4(r.config)),
    [a, o] = CodexProviderPatchReact.useState(r.error);
  CodexProviderPatchReact.useEffect(() => {
    codexPickerLoadProviderRoutingConfigV4(!0).then((r) => {
      (t(r), i(codexReadProviderChoiceV4(r)), o(codexPickerProviderRoutingStateV4().error));
    });
  }, []);
  CodexProviderPatchReact.useEffect(() => {
    let e = () => i(codexReadProviderChoiceV4(codexPickerProviderRoutingStateV4().config));
    return (window.addEventListener(`codex.customProviderSelection.v2.change`, e), () => {
      window.removeEventListener(`codex.customProviderSelection.v2.change`, e);
    });
  }, []);
  if (e.providers.length < 2 && a == null) return null;
  return (0, wQ.jsxs)(wQ.Fragment, {
    children: [
      a == null ? null : (0, wQ.jsx)(yz.Item, {
        disabled: !0,
        SubText: (0, wQ.jsx)(`span`, {
          className: `text-token-description-foreground`,
          children: a,
        }),
        children: `Provider config error`,
      }),
      e.providers.length < 2 ? null : (0, wQ.jsx)(yz.Title, { children: `Provider for new tasks` }),
      e.providers.length < 2 ? null : e.providers.map((e) => (0, wQ.jsx)(yz.Item, {
        RightIcon: n === e.id ? Ym : void 0,
        SubText: (0, wQ.jsx)(`span`, {
          className: `text-token-description-foreground`,
          children: e.description || `Custom Provider`,
        }),
        onSelect: (r) => {
          (r?.preventDefault(), codexWriteProviderChoiceV4(e.id), i(e.id));
          void Rf(`clear-prewarmed-threads-for-host`, { hostId: `local` }).catch(() => {});
        },
        children: e.label,
      }, e.id)),
      (0, wQ.jsx)(yz.Separator, {}),
    ],
  });
}
async function codexUpdateConfigModelProvider(e, reload = true) {
  console.error(`[codex-provider-patch] switching to provider: ${e}`);
  try {
    let cfg = await codexLoadProviderRoutingConfigV4(!0);
    let p = cfg.providers.find((x) => x.id === e) ?? cfg.providers.find((x) => x.id === cfg.defaultProvider);
    if (p == null || !p.base_url || !p.token) {
      console.error(`[codex-provider-patch] no base_url/token for provider ${e} in desktop-model-providers.json`);
      return;
    }
    let newModel = p.models.length > 0 ? p.models[0].id : ``;
    let { codexHome: h } = await tp(`codex-home`, { params: { hostId: `local` } }),
      sep = h.includes(`\\`) && !h.includes(`/`) ? `\\` : `/`,
      path = `${h.replace(/[\\/]+$/u, ``)}${sep}config.toml`,
      { contents: raw } = await tp(`read-file`, { params: { hostId: `local`, path } }),
      lines = raw.split(`\n`),
      inCodexier = false, baseUrlLine = -1, tokenLine = -1, modelLine = -1;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].trim() === `[model_providers.codexier]`) inCodexier = true;
      else if (inCodexier && lines[i].trim().startsWith(`[`)) break;
      else if (inCodexier) {
        if (lines[i].match(/^\s*base_url\s*=/)) baseUrlLine = i;
        else if (lines[i].match(/^\s*experimental_bearer_token\s*=/)) tokenLine = i;
      }
      if (lines[i].match(/^\s*model\s*=\s*"/) && !inCodexier) modelLine = i;
    }
    if (baseUrlLine === -1 || tokenLine === -1) {
      console.error(`[codex-provider-patch] could not locate base_url/token lines in [model_providers.codexier]`);
      return;
    }
    lines[baseUrlLine] = lines[baseUrlLine].replace(/"[^"]*"/, `"${p.base_url}"`);
    lines[tokenLine] = lines[tokenLine].replace(/"[^"]*"/, `"${p.token}"`);
    if (newModel && modelLine >= 0) {
      lines[modelLine] = lines[modelLine].replace(/"[^"]*"/, `"${newModel}"`);
      console.error(`[codex-provider-patch] model set to ${newModel}`);
    }
    await tp(`write-file`, { params: { hostId: `local`, path, contents: lines.join(`\n`) } });
    console.error(`[codex-provider-patch] config.toml written: base_url + token + model`);
    if (reload) {
      console.error(`[codex-provider-patch] reloading to apply new credentials`);
      setTimeout(() => window.location.reload(), 500);
    }
  } catch (err) {
    console.error(`[codex-provider-patch] switch failed:`, String(err));
  }
}
async function codexSyncConfigOnLoad() {
  if (window.__codexStartupSyncDone) return;
  window.__codexStartupSyncDone = true;
  try {
    let stored = window.localStorage.getItem(`codex.customProviderSelection.v2`);
    if (!stored) return;
    console.error(`[codex-provider-patch] startup sync for provider: ${stored}`);
    let reloadKey = `codex.startupSyncReload.${stored}`;
    let alreadyReloaded = window.localStorage.getItem(reloadKey) === `1`;
    await codexUpdateConfigModelProvider(stored, !alreadyReloaded);
    if (!alreadyReloaded) {
      try { window.localStorage.setItem(reloadKey, `1`); } catch {}
    }
  } catch (err) {
    console.error(`[codex-provider-patch] startup sync failed:`, String(err));
  }
}
function codexWriteProviderChoiceV4(e) {
  try {
    window.localStorage.setItem(`codex.customProviderSelection.v2`, e);
    let reloadKey = `codex.startupSyncReload.${e}`;
    window.localStorage.removeItem(reloadKey);
  } catch {}
  window.dispatchEvent(new Event(`codex.customProviderSelection.v2.change`));
  codexUpdateConfigModelProvider(e, true).catch((err) => {
    console.error(`[codex-provider-patch] unhandled error:`, String(err));
  });
}"""

CODEX_26721_4979_LAYOUT = "Codex 26.721.4979 provider-first picker"
CODEX_26721_4979_CENTRAL_ANCHOR = (
    "function s9t(e){if(`data`in e)return e;let t=abe(e);"
    "return t==null?e:{...e,data:t}}var c9t,l9t,u9t,d9t,f9t,p9t,"
)
CODEX_26721_4979_REQUEST_ANCHOR = (
    "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error("
    "`AppServerRequestClient is missing a message dispatcher`);return "
    "e===`config/read`?"
)
CODEX_26721_4979_PREWARM_ANCHOR = (
    "async prewarmThreadStart(e,t){if(this.dispatchMessage==null)throw Error("
    "`AppServerRequestClient is missing a message dispatcher`);let n="
)
CODEX_26721_4979_PICKER_ANCHOR = "function dMs(e){"
CODEX_26721_4979_MODELS_ANCHOR = (
    "triggerButton:N}=e,P=m===void 0?!1:m,F=E===void 0?!1:E,"
)
CODEX_26721_4979_MENU_ANCHOR = (
    "children:[m,(0,TQ.jsx)(`div`,{className:"
    "`vertical-scroll-fade-mask flex max-h-[250px] flex-col overflow-y-auto`,"
)
CODEX_26721_4979_MODEL_CHANGED_ANCHOR = (
    "t[0]!==n||t[1]!==a?(o=jol(n,a),t[0]=n,t[1]=a,t[2]=o):o=t[2];"
    "let s=o,c=i?.models,l;t[3]!==c||t[4]!==r?(l=jol(r,c),t[3]=c,t[4]=r,"
    "t[5]=l):l=t[5];"
)
CODEX_26721_5848_MODEL_LABEL_ANCHOR = (
    "function Uol(e,t){let n=GM(t,e)?.displayName;"
    "return n!=null&&n.trim().length>0?GX(n):(0,P6.jsx)(Z,{"
    "id:`composer.mode.local.model.custom`,defaultMessage:`Custom`,"
    "description:`Custom model from config`})}"
)
CODEX_26721_5848_COMPOSER_LABEL_ANCHOR = (
    "if(r!=null){let e;if(t[0]!==r||t[1]!==c){let n=GX(r);"
    "e=c?n.replace(/^GPT-/iu,``):n,t[0]=r,t[1]=c,t[2]=e}"
    "else e=t[2];l=e}else if(n){let e;"
    "t[3]===Symbol.for(`react.memo_cache_sentinel`)?"
    "(e=(0,Lcs.jsx)(Z,{id:`composer.mode.local.model.custom`,"
    "defaultMessage:`Custom`,description:`Custom model from config`}),"
    "t[3]=e):e=t[3],l=e}else l=n;let u;"
)
CODEX_26721_5848_SUBMENU_ANCHOR = (
    "function Scs(e){let t=(0,wcs.c)(12),{submenu:n}=e,r=n.ariaLabel,"
    "i=n.contentClassName,a=n.disabled,o;t[0]===n.title?o=t[1]:"
    "(o=n.title==null?null:(0,QX.jsx)(yz.Title,{children:n.title}),"
    "t[0]=n.title,t[1]=o);let s=n.label,c=n.value,l;t[2]===n.options?"
    "l=t[3]:(l=n.options.map(Ccs),t[2]=n.options,t[3]=l);let u;return "
    "t[4]!==n.ariaLabel||t[5]!==n.contentClassName||t[6]!==n.disabled||"
    "t[7]!==n.label||t[8]!==n.value||t[9]!==o||t[10]!==l?"
    "(u=(0,QX.jsx)(Kos,{ariaLabel:r,contentClassName:i,disabled:a,"
    "flyoutHeader:o,label:s,value:c,children:l}),t[4]=n.ariaLabel,"
    "t[5]=n.contentClassName,t[6]=n.disabled,t[7]=n.label,t[8]=n.value,"
    "t[9]=o,t[10]=l,t[11]=u):u=t[11],u}"
)
CODEX_26721_4979_REACT_ANCHOR = (
    "var pMs,TQ,mMs=e((()=>{pMs=c(),pd(),ad(),uls(),yss(),bss(),VAs(),Xm(),"
    "qX(),dD(),bz(),Hos(),Mcs(),ycs(),zos(),Zos(),Kos(),kcs(),TQ=J()})),"
)

CODEX_26721_5848_LAYOUT = "Codex 26.721.41059 macOS build 5848 provider picker"

# These are deliberately generated rather than hand-written unified-diff
# lines.  It prevents an accidental missing `+` from creating an invalid
# embedded patch while retaining exact source-hunk matching.
CENTRAL_DIFF_26721_V7 = _replace_added_block(
    CENTRAL_DIFF_26721,
    "function codexProviderRoutingFallback() {",
    "}\nvar s9t,\n  c9t,",
    CENTRAL_V7_JAVASCRIPT.rsplit("\n", 1)[0],
)
PICKER_DIFF_26721_V7 = _replace_added_block(
    PICKER_DIFF_26721,
    "function codexPickerProviderRoutingFallback() {",
    "function CMs(e) {",
    PICKER_V7_JAVASCRIPT,
)
PICKER_DIFF_26721_V7 = _insert_hunk_before(
    PICKER_DIFF_26721_V7,
    "@@ -549693,6 +549895,7 @@\n",
    _append_insertion_diff(
        "",
        "      triggerButton: N,\n    } = e,\n    P = m === void 0 ? !1 : m,",
        "    p = codexUseProviderModels(p, d, y),",
    ),
)

PATCH_VARIANTS: tuple[tuple[str, str, str], ...] = (
    ("ChatGPT 26.721 provider-first picker", CENTRAL_DIFF_26721_V7, PICKER_DIFF_26721_V7),
)


def parse_hunks(unified_diff: str) -> list[list[str]]:
    lines = unified_diff.splitlines()
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("@@ "):
            current = []
            hunks.append(current)
        elif current is not None:
            if not line or line[0] not in " +-":
                raise PatchError(f"Malformed embedded diff line: {line!r}")
            current.append(line)
    if not hunks:
        raise PatchError("Embedded patch contains no hunks")
    return hunks


def _compact_javascript(source: str) -> tuple[str, list[int]]:
    """Remove layout outside literals while retaining source offsets.

    This is deliberately not a JavaScript parser. It only makes exact patch
    hunks resilient to minified-versus-formatted layout, without loading an
    entire Electron bundle into Prettier's JavaScript AST.
    """
    compact: list[str] = []
    positions: list[int] = []
    index = 0
    quote: str | None = None
    while index < len(source):
        character = source[index]
        if quote is not None:
            compact.append(character)
            positions.append(index)
            if character == "\\" and index + 1 < len(source):
                index += 1
                compact.append(source[index])
                positions.append(index)
            elif character == quote:
                quote = None
            index += 1
            continue
        if character in {"'", '"', "`"}:
            quote = character
        elif character.isspace():
            index += 1
            continue
        compact.append(character)
        positions.append(index)
        index += 1
    return "".join(compact), positions


def _render_layout_independent_hunk(
    source: str,
    old_lines: list[str],
    new_lines: list[str],
    source_name: str,
    hunk_number: int,
) -> str:
    old_compact, _ = _compact_javascript("\n".join(old_lines))
    source_compact, offsets = _compact_javascript(source)
    matches: list[int] = []
    start = 0
    while (index := source_compact.find(old_compact, start)) != -1:
        matches.append(index)
        start = index + 1
    if len(matches) != 1:
        raise PatchError(
            f"{source_name}: hunk {hunk_number} matched {len(matches)} times; "
            "the app build is unsupported or already modified"
        )
    first = offsets[matches[0]]
    last = offsets[matches[0] + len(old_compact) - 1] + 1
    return source[:first] + "\n".join(new_lines) + source[last:]


def render_unified_diff(source: str, unified_diff: str, source_name: str) -> str:
    had_trailing_newline = source.endswith("\n")
    source_lines = source.splitlines()
    search_start = 0

    for hunk_number, hunk in enumerate(parse_hunks(unified_diff), start=1):
        old_lines = [line[1:] for line in hunk if line[0] in " -"]
        new_lines = [line[1:] for line in hunk if line[0] in " +"]
        matches = [
            index
            for index in range(search_start, len(source_lines) - len(old_lines) + 1)
            if source_lines[index : index + len(old_lines)] == old_lines
        ]
        if len(matches) != 1:
            source = _render_layout_independent_hunk(
                "\n".join(source_lines) + ("\n" if had_trailing_newline else ""),
                old_lines,
                new_lines,
                source_name,
                hunk_number,
            )
            source_lines = source.splitlines()
            search_start = 0
            continue
        index = matches[0]
        source_lines[index : index + len(old_lines)] = new_lines
        search_start = index + len(new_lines)

    return "\n".join(source_lines) + ("\n" if had_trailing_newline else "")


def _replace_once(source: str, old: str, new: str, layout: str) -> str:
    if source.count(old) != 1:
        raise PatchError(f"{layout}: required source anchor was not unique")
    return source.replace(old, new, 1)


def _apply_codex_26721_4979_layout(source: str) -> str:
    """Apply source-validated routing to the merged 26.721.4979 web bundle."""
    layout = CODEX_26721_4979_LAYOUT
    source = _replace_once(
        source,
        CODEX_26721_4979_CENTRAL_ANCHOR,
        CODEX_26721_4979_CENTRAL_ANCHOR.replace(
            "var c9t,",
            f"{CENTRAL_V7_JAVASCRIPT}\nvar c9t,",
        ),
        layout,
    )
    source = _replace_once(
        source,
        CODEX_26721_4979_REQUEST_ANCHOR,
        CODEX_26721_4979_REQUEST_ANCHOR.replace(
            ");return ", ");t=await codexPatchAppServerParams(e,t);return "
        ),
        layout,
    )
    source = _replace_once(
        source,
        CODEX_26721_4979_PREWARM_ANCHOR,
        CODEX_26721_4979_PREWARM_ANCHOR.replace(
            ");let n=", ");e=await codexPatchAppServerParams(`thread/start`,e);let n="
        ),
        layout,
    )
    source = _replace_once(
        source,
        CODEX_26721_4979_PICKER_ANCHOR,
        f"{PICKER_V7_JAVASCRIPT.replace('wQ', 'TQ')}\n{CODEX_26721_4979_PICKER_ANCHOR}",
        layout,
    )
    source = _replace_once(
        source,
        CODEX_26721_4979_MODELS_ANCHOR,
        CODEX_26721_4979_MODELS_ANCHOR.replace(
            "}=e,P=m", "}=e;p=codexUseProviderModels(p,d,y);let P=m"
        ),
        layout,
    )
    source = _replace_once(
        source,
        CODEX_26721_4979_MENU_ANCHOR,
        CODEX_26721_4979_MENU_ANCHOR.replace(
            "children:[m,", "children:[m,(0,TQ.jsx)(CodexCustomProviderPickerSection,{}),"
        ),
        layout,
    )
    source = _replace_once(
        source,
        CODEX_26721_4979_MODEL_CHANGED_ANCHOR,
        CODEX_26721_4979_MODEL_CHANGED_ANCHOR.replace(
            "o=jol(n,a)", "o=codexPickerModelLabelV4(n,jol(n,a))"
        ).replace(
            "l=jol(r,c)", "l=codexPickerModelLabelV4(r,jol(r,c))"
        ),
        layout,
    )
    return _replace_once(
        source,
        CODEX_26721_4979_REACT_ANCHOR,
        CODEX_26721_4979_REACT_ANCHOR.replace(
            "var pMs,TQ,",
            "var pMs,TQ,CodexProviderPatchReact,",
        ).replace(
            "pMs=c(),pd()",
            "pMs=c(),CodexProviderPatchReact=pMs,pd()",
        ),
        layout,
    )


def apply_supported_patch_variant(central: Path, picker: Path) -> str:
    originals = {
        path: path.read_text(encoding="utf-8") for path in {central, picker}
    }
    compatible: list[tuple[str, dict[Path, str]]] = []

    if central == picker:
        try:
            source = originals[central]
            source = _replace_once(
                source,
                "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,n)}",
                "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,await codexPatchAppServerParams(e,t),n)}",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "async prewarmThreadStart(e,t){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);let n=",
                "async prewarmThreadStart(e,t){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);e=await codexPatchAppServerParams(`thread/start`,e);let n=",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "function dMs(e){",
                CENTRAL_V7_JAVASCRIPT
                + "\n"
                + PICKER_V7_JAVASCRIPT
                + "\nfunction dMs(e){",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "var TMs,wQ,EMs=e((()=>{TMs=c(),",
                "var TMs,wQ,CodexProviderPatchReact,EMs=e((()=>{TMs=c(),CodexProviderPatchReact=r(o(),1),",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                CODEX_26721_5848_SUBMENU_ANCHOR,
                CODEX_26721_5848_SUBMENU_ANCHOR
                .replace("(12)", "(13)", 1)
                .replace(
                    "t[9]!==o||t[10]!==l?",
                    "t[9]!==o||t[10]!==l||t[11]!==n.extras?",
                    1,
                )
                .replace(
                    "children:l}),",
                    "children:n.extras==null?l:(0,QX.jsxs)"
                    "(QX.Fragment,{children:[n.extras,l]})}),",
                    1,
                )
                .replace(
                    "t[9]=o,t[10]=l,t[11]=u):u=t[11]",
                    "t[9]=o,t[10]=l,t[11]=n.extras,t[12]=u):u=t[12]",
                    1,
                ),
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "let z=R,B=A===void 0?!1:A,V=j===void 0?!0:j,H=M===void 0?!1:M,U=ed(),",
                "p=codexUseProviderModels(p,d,y);let z=R,B=A===void 0?!1:A,V=j===void 0?!0:j,H=M===void 0?!1:M,U=ed(),",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "},model:{ariaLabel:U.formatMessage(",
                "},model:{extras:(0,wQ.jsx)(CodexCustomProviderPickerSection,{}),"
                "ariaLabel:U.formatMessage(",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "children:[m,(0,wQ.jsx)(`div`,{className:`vertical-scroll-fade-mask",
                "children:[m,(0,wQ.jsx)(`div`,{className:`vertical-scroll-fade-mask",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "contentClassName:`w-[280px]`,disabled:P||p==null,children:re",
                "contentClassName:`w-[280px]`,disabled:P||p==null,flyoutHeader:(0,wQ.jsx)(CodexCustomProviderPickerSection,{}),children:re",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "contentClassName:`w-[280px]`,disabled:fe,children:re",
                "contentClassName:`w-[280px]`,disabled:fe,flyoutHeader:(0,wQ.jsx)(CodexCustomProviderPickerSection,{}),children:re",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                CODEX_26721_5848_MODEL_LABEL_ANCHOR,
                "function CodexProviderModelLabelV4(e){let[,t]="
                "CodexProviderPatchReact.useState(0);"
                "CodexProviderPatchReact.useEffect(()=>{let e=()=>t(e=>e+1);"
                "return window.addEventListener(`codex.customProviderSelection.v2.change`,e),"
                "window.addEventListener(`codex.customProviderRouting.v4.change`,e),"
                "()=>{window.removeEventListener(`codex.customProviderSelection.v2.change`,e);"
                "window.removeEventListener(`codex.customProviderRouting.v4.change`,e)}},[]);"
                "let n=codexPickerModelLabelV4(e.value,e.fallback);"
                "return n!=null&&n.trim().length>0?GX(n):(0,P6.jsx)(Z,{"
                "id:`composer.mode.local.model.custom`,defaultMessage:`Custom`,"
                "description:`Custom model from config`})}"
                "function Uol(e,t){return (0,P6.jsx)"
                "(CodexProviderModelLabelV4,{value:e,fallback:GM(t,e)?.displayName})}",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                CODEX_26721_5848_COMPOSER_LABEL_ANCHOR,
                "if(n){l=(0,Lcs.jsx)(CodexProviderModelLabelV4,{value:n,"
                "fallback:r??n})}else if(r!=null){let e;if(t[0]!==r||t[1]!==c)"
                "{let n=GX(r);e=c?n.replace(/^GPT-/iu,``):n,t[0]=r,t[1]=c,"
                "t[2]=e}else e=t[2];l=e}else l=n;let u;",
                CODEX_26721_5848_LAYOUT,
            )
            compatible.append((CODEX_26721_5848_LAYOUT, {central: source}))
        except PatchError:
            pass

    if central == picker:
        try:
            compatible.append(
                (CODEX_26721_4979_LAYOUT, {central: _apply_codex_26721_4979_layout(originals[central])})
            )
        except PatchError:
            pass

    for name, central_diff, picker_diff in PATCH_VARIANTS:
        rendered = originals.copy()
        try:
            rendered[central] = render_unified_diff(
                rendered[central], central_diff, central.name
            )
            rendered[picker] = render_unified_diff(
                rendered[picker], picker_diff, picker.name
            )
        except PatchError:
            continue
        compatible.append((name, rendered))

    if len(compatible) != 1:
        raise PatchError(
            "Expected exactly one supported JavaScript patch layout, found "
            f"{len(compatible)}. This app build is unsupported or already modified."
        )

    name, rendered = compatible[0]
    for path, source in rendered.items():
        path.write_text(source, encoding="utf-8")
    return name


