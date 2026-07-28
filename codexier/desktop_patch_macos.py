#!/usr/bin/env python3
"""Install the custom model-provider picker patch into ChatGPT.app on macOS.

The patch is intentionally version-sensitive: it only edits JavaScript bundles
whose expected source hunks match exactly. App updates that change those bundles
cause a clean failure before the installed app is modified.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import plistlib
import re
import signal
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import Any, NoReturn

try:
    from .patch_progress import PatchProgress, report
except ImportError:  # Support running this installer directly as a script.
    from patch_progress import PatchProgress, report

try:
    from .backup import immutable_file_backup
except ImportError:  # Support running this installer directly as a script.
    from backup import immutable_file_backup

try:
    import pwd
except ImportError:  # Windows imports the shared source-validation helpers.
    pwd = None


PATCH_MARKER = b"__codexDesktopModelProvidersPatchV8"
LEGACY_PATCH_MARKERS = (
    b"__codexDesktopModelProvidersPatchV7",
    b"__codexDesktopModelProvidersPatchV2",
    b"__codexDesktopModelProvidersPatchV3",
    b"__codexDesktopModelProvidersPatchV4",
    b"__codexDesktopModelProvidersPatchV5",
    b"__codexDesktopModelProvidersPatchV6",
)
ASAR_PACKAGE = "@electron/asar@3.2.10"

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


CENTRAL_DIFF_V2_TO_V3 = r"""@@ -137601,7 +137601,7 @@
   };
 }
 function codexProviderRoutingState() {
-  return (window.__codexDesktopModelProvidersPatchV2 ??= {
+  return (window.__codexDesktopModelProvidersPatchV3 ??= {
     config: codexProviderRoutingFallback(),
     configPath: null,
     error: null,
"""


PICKER_DIFF_26721_V2_TO_V3 = r"""@@ -520849,7 +520849,7 @@
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
@@ -549630,7 +549636,7 @@
   };
 }
 function codexPickerProviderRoutingState() {
-  return (window.__codexDesktopModelProvidersPatchV2 ??= {
+  return (window.__codexDesktopModelProvidersPatchV3 ??= {
     config: codexPickerProviderRoutingFallback(),
     configPath: null,
     error: null,
@@ -549886,7 +549892,6 @@
         (t[39] = f))
       : (f = t[39]),
       (G = {
-        extras: (0, wQ.jsx)(CodexCustomProviderPickerSection, {}),
         effort: {
           ariaLabel: U.formatMessage(
             {
@@ -549921,6 +549926,7 @@
           value: s,
         },
         model: {
+          extras: (0, wQ.jsx)(CodexCustomProviderPickerSection, {}),
           ariaLabel: U.formatMessage(
             {
               id: `composer.intelligenceDropdown.model.rowAriaLabel`,
@@ -550013,6 +550019,7 @@
       ? (g = t[52])
       : ((g = (0, wQ.jsxs)(wQ.Fragment, {
           children: [
+            (0, wQ.jsx)(CodexCustomProviderPickerSection, {}),
             m,
             (0, wQ.jsx)(`div`, {
               className: `vertical-scroll-fade-mask flex max-h-[250px] flex-col overflow-y-auto`,
@@ -550148,7 +550155,6 @@
       : (k = t[77]),
       (K = (0, wQ.jsxs)(wQ.Fragment, {
         children: [
-          (0, wQ.jsx)(CodexCustomProviderPickerSection, {}),
           (0, wQ.jsx)(Kos, {
             ariaLabel: U.formatMessage(
               {
@@ -550354,7 +550360,6 @@
   t[90] !== le || t[91] !== pe || t[92] !== me
     ? ((he = (0, wQ.jsxs)(wQ.Fragment, {
         children: [
-          (0, wQ.jsx)(CodexCustomProviderPickerSection, {}),
           le,
           pe,
           me,
"""


PICKER_DIFF_LEGACY_V2_TO_V3 = r"""@@ -10242,7 +10242,7 @@
   };
 }
 function codexPickerProviderRoutingState() {
-  return (window.__codexDesktopModelProvidersPatchV2 ??= {
+  return (window.__codexDesktopModelProvidersPatchV3 ??= {
     config: codexPickerProviderRoutingFallback(),
     configPath: null,
     error: null,
@@ -10360,6 +10360,6 @@
 function MO(e) {
   let t = (0, PO.c)(169),
     {
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
    t.push({
      id: e,
      label: typeof r.label === `string` && r.label.trim().length > 0 ? r.label.trim() : e,
      description: typeof r.description === `string` ? r.description.trim() : ``,
      models: a,
    });
  }
  let r = typeof e.default_provider === `string` ? e.default_provider.trim() : ``;
  if (!n.has(r)) throw Error(`default_provider must reference a configured provider`);
  return { version: 2, defaultProvider: r, providers: t };
}
function codexProviderRoutingStateV4() {
  return (window.__codexDesktopModelProvidersPatchV8 ??= {
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
  if (e !== `thread/start` || t == null || typeof t !== `object`) return t;
  let n = await codexLoadProviderRoutingConfigV4(!0), r;
  try { r = window.localStorage.getItem(`codex.customProviderSelection.v2`); } catch {}
  let i = n.providers.find((e) => e.id === r) ?? n.providers.find((e) => e.id === n.defaultProvider);
  if (i == null) return t;
  if (!i.models.some((e) => e.id === t.model)) return t;
  return { ...t, modelProvider: i.id };
}"""

PICKER_V7_JAVASCRIPT = r"""function codexPickerProviderRoutingFallbackV4() {
  return {
    version: 2,
    defaultProvider: null,
    providers: [],
  };
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
    t.push({
      id: e,
      label: typeof r.label === `string` && r.label.trim().length > 0 ? r.label.trim() : e,
      description: typeof r.description === `string` ? r.description.trim() : ``,
      models: a,
    });
  }
  let r = typeof e.default_provider === `string` ? e.default_provider.trim() : ``;
  if (!n.has(r)) throw Error(`default_provider must reference a configured provider`);
  return { version: 2, defaultProvider: r, providers: t };
}
function codexPickerProviderRoutingStateV4() {
  return (window.__codexDesktopModelProvidersPatchV8 ??= {
    config: codexPickerProviderRoutingFallbackV4(), error: null, loaded: !1, promise: null,
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
      return ((t.config = a), (t.error = null), (t.loaded = !0), a);
    } catch (e) {
      return ((t.config = codexPickerProviderRoutingFallbackV4()), (t.error = e instanceof Error ? e.message : String(e)), (t.loaded = !0), t.config);
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
function codexWriteProviderChoiceV4(e) {
  try { window.localStorage.setItem(`codex.customProviderSelection.v2`, e); } catch {}
}
function codexPickerModelLabelV4(e, t) {
  let n = codexPickerProviderRoutingStateV4().config,
    r = codexReadProviderChoiceV4(n),
    i = n.providers.find((e) => e.id === r);
  for (let a of [i, ...n.providers]) {
    let r = a?.models.find((t) => t.id === e);
    if (r != null) return `${r.label} (${a.label})`;
  }
  return t;
}
function codexUseProviderModels(e) {
  let r = codexPickerProviderRoutingStateV4(),
    [t, n] = CodexProviderPatchReact.useState(r.config),
    [i, a] = CodexProviderPatchReact.useState(() => codexReadProviderChoiceV4(r.config));
  CodexProviderPatchReact.useEffect(() => {
    let e = !0;
    return (codexPickerLoadProviderRoutingConfigV4(!0).then((t) => {
      e && (n(t), a(codexReadProviderChoiceV4(t)));
    }), () => { e = !1; });
  }, []);
  let o = t.providers.find((e) => e.id === i) ?? t.providers.find((e) => e.id === t.defaultProvider);
  if (o == null || !Array.isArray(e) || e.length === 0) return e;
  return o.models.map((t) => ({
    ...e[0],
    id: t.id,
    model: t.id,
    name: t.label,
    label: t.label,
    displayName: `${t.label} (${o.label})`,
  }));
}
function CodexCustomProviderPickerSection() {
  let r = codexPickerProviderRoutingStateV4(),
    [e, t] = CodexProviderPatchReact.useState(r.config),
    [n, i] = CodexProviderPatchReact.useState(() => codexReadProviderChoiceV4(r.config));
  CodexProviderPatchReact.useEffect(() => {
    codexPickerLoadProviderRoutingConfigV4(!0).then((r) => {
      (t(r), i(codexReadProviderChoiceV4(r)));
    });
  }, []);
  if (e.providers.length < 2) return null;
  return (0, wQ.jsxs)(wQ.Fragment, {
    children: [
      (0, wQ.jsx)(yz.Title, { children: `Provider for new tasks` }),
      e.providers.map((e) => (0, wQ.jsx)(yz.Item, {
        RightIcon: n === e.id ? Ym : void 0,
        SubText: (0, wQ.jsx)(`span`, {
          className: `text-token-description-foreground`,
          children: e.description || `Choose this provider, then choose one of its models`,
        }),
        onSelect: (r) => {
          (r?.preventDefault(), codexWriteProviderChoiceV4(e.id), i(e.id));
        },
        children: e.label,
      }, e.id)),
      (0, wQ.jsx)(yz.Separator, {}),
    ],
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
        "    p = codexUseProviderModels(p),",
    ),
)

def _v7_upgrade_diffs(
    marker: str, heading: str, *, remove_thread_list: bool = False
 ) -> tuple[str, str]:
    central = rf"""@@ provider fallback
 function codexProviderRoutingFallbackV4() {{
   return {{
     version: 2,
-    defaultProvider: `openai`,
-    providers: [{{ id: `openai`, label: `ChatGPT / OpenAI`, description: `Uses your signed-in ChatGPT account`, models: [] }}],
+    defaultProvider: null,
+    providers: [],
   }};
 }}
@@ provider marker
 function codexProviderRoutingStateV4() {{
-  return (window.__codexDesktopModelProvidersPatch{marker} ??= {{
+  return (window.__codexDesktopModelProvidersPatchV8 ??= {{
     config: codexProviderRoutingFallbackV4(), error: null, loaded: !1, promise: null,
   }});
"""
    if remove_thread_list:
        central += r"""@@ thread safety
 async function codexPatchAppServerParams(e, t) {
-  if (e === `thread/list`) {
-    let e = t != null && typeof t === `object` ? t : {};
-    return e.modelProviders == null ? { ...e, modelProviders: [] } : e;
-  }
   if (e !== `thread/start` || t == null || typeof t !== `object`) return t;
"""
    central += r"""@@ provider validation
   let i = n.providers.find((e) => e.id === r) ?? n.providers.find((e) => e.id === n.defaultProvider);
   if (i == null) return t;
-  if (i.id !== `openai` && !i.models.some((e) => e.id === t.model))
+  if (!i.models.some((e) => e.id === t.model))
     throw Error(`The selected model is not configured for the selected provider`);
"""
    picker = r"""@@ picker fallback
 function codexPickerProviderRoutingFallbackV4() {
   return {
     version: 2,
-    defaultProvider: `openai`,
-    providers: [{ id: `openai`, label: `ChatGPT / OpenAI`, description: `Uses your signed-in ChatGPT account`, models: [] }],
+    defaultProvider: null,
+    providers: [],
   };
 }
@@ native model preservation
   let o = t.providers.find((e) => e.id === i) ?? t.providers.find((e) => e.id === t.defaultProvider);
   if (o == null) return e;
-  if (o.id === `openai`) {
-    let t = new Set(r.config.providers.flatMap((e) => e.id === `openai` ? [] : e.models.map((e) => e.id)));
-    return e?.filter((e) => !t.has(e.model));
-  }
   return o.models.flatMap((t) => {
"""
    if heading == "Provider":
        picker += r"""@@ provider heading
-      (0, wQ.jsx)(yz.Title, { children: `Provider` }),
+      (0, wQ.jsx)(yz.Title, { children: `Provider for new tasks` }),
"""
    return central, picker


CENTRAL_DIFF_V4_TO_V7, PICKER_DIFF_V4_TO_V7 = _v7_upgrade_diffs(
    "V4", "Provider", remove_thread_list=True
 )
CENTRAL_DIFF_V5_TO_V7, PICKER_DIFF_V5_TO_V7 = _v7_upgrade_diffs("V5", "Provider")
CENTRAL_DIFF_V6_TO_V7, PICKER_DIFF_V6_TO_V7 = _v7_upgrade_diffs(
    "V6", "Provider for new tasks"
 )


PATCH_VARIANTS: tuple[tuple[str, str, str], ...] = (
    ("ChatGPT 26.721 V6 provider cleanup upgrade", CENTRAL_DIFF_V6_TO_V7, PICKER_DIFF_V6_TO_V7),
    ("ChatGPT 26.721 V5 provider cleanup upgrade", CENTRAL_DIFF_V5_TO_V7, PICKER_DIFF_V5_TO_V7),
    ("ChatGPT 26.721 V4 provider cleanup upgrade", CENTRAL_DIFF_V4_TO_V7, PICKER_DIFF_V4_TO_V7),
    ("ChatGPT 26.721 V7 provider-first picker", CENTRAL_DIFF_26721_V7, PICKER_DIFF_26721_V7),
)


class PatchError(RuntimeError):
    """A safe, expected patch failure."""


class PatchSkipped(PatchError):
    """A safe patch skip that leaves the desktop application untouched."""


def colors_enabled(stream: Any = sys.stdout) -> bool:
    return "NO_COLOR" not in os.environ and (
        getattr(stream, "isatty", lambda: False)()
        or os.environ.get("FORCE_COLOR") not in (None, "", "0")
    )


def color(text: object, *codes: str, stream: Any = sys.stdout) -> str:
    rendered = str(text)
    if not colors_enabled(stream) or not codes:
        return rendered
    return f"\033[{';'.join(codes)}m{rendered}\033[0m"


def terminal_width() -> int:
    return max(64, min(shutil.get_terminal_size((96, 24)).columns, 110))


def terminal_status(
    label: str,
    message: object,
    code: str,
    *,
    detail: object | None = None,
    stream: Any = sys.stdout,
) -> None:
    badge_width = 10
    plain_badge = f"[{label}]"
    badge = color(plain_badge, "1", code, stream=stream)
    badge_padding = " " * max(1, badge_width - len(plain_badge))
    available = max(30, terminal_width() - badge_width)
    lines = textwrap.wrap(
        str(message),
        width=available,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    print(f"{badge}{badge_padding}{lines[0]}", file=stream)
    for line in lines[1:]:
        print(f"{'':{badge_width}}{line}", file=stream)
    if detail is not None:
        detail_lines = textwrap.wrap(
            str(detail),
            width=max(30, terminal_width() - badge_width - 2),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        for index, line in enumerate(detail_lines):
            marker = "↳ " if index == 0 else "  "
            print(
                f"{'':{badge_width}}{color(marker + line, '2', stream=stream)}",
                file=stream,
            )
    stream.flush()


def terminal_heading(title: str, code: str = "36") -> None:
    visible_title = f" {title.upper()} "
    rule_length = max(2, terminal_width() - len(visible_title))
    print()
    print(
        color(f"{visible_title}{'━' * rule_length}", "1", code),
    )
    sys.stdout.flush()


def terminal_panel(
    title: str,
    message: object,
    code: str,
    *,
    stream: Any = sys.stderr,
) -> None:
    width = terminal_width()
    title_text = f" {title.upper()} "
    top = f"╭─{title_text}{'─' * max(1, width - len(title_text) - 2)}"
    bottom = f"╰{'─' * (width - 1)}"
    print(file=stream)
    print(color(top, "1", code, stream=stream), file=stream)
    paragraphs = str(message).splitlines() or [""]
    for paragraph in paragraphs:
        wrapped = textwrap.wrap(
            paragraph,
            width=max(30, width - 4),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        for line in wrapped:
            border = color("│", code, stream=stream)
            print(f"{border} {color(line, '1', stream=stream)}", file=stream)
    print(color(bottom, "1", code, stream=stream), file=stream)
    print(file=stream)
    stream.flush()


def terminal_bullet(label: str, description: str) -> None:
    bullet = color("◆", "1", "36")
    key = color(label, "1", "33")
    prefix_width = 29
    prefix = f"  {bullet} {key}"
    padding = " " * max(1, prefix_width - 4 - len(label))
    available = max(30, terminal_width() - prefix_width)
    lines = textwrap.wrap(
        description,
        width=available,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    print(f"{prefix}{padding}{lines[0]}")
    for line in lines[1:]:
        print(f"{'':{prefix_width}}{line}")
    sys.stdout.flush()


def print_completion_summary(
    config: Path,
    *,
    backup: Path | None = None,
    already_installed: bool = False,
    upgraded: bool = False,
) -> None:
    codex_config = config.parent / "config.toml"
    if already_installed:
        terminal_status(
            "READY",
            "Patch already installed; no app files were changed.",
            "32",
        )
    else:
        terminal_status(
            "SUCCESS",
            "Patch upgraded successfully."
            if upgraded
            else "Patch installed successfully.",
            "32",
        )

    terminal_heading("Custom provider config")
    terminal_status("CONFIG", "Codexier manages this provider/model config:", "36", detail=config)
    terminal_bullet("providers", "Enabled providers displayed in the app menu.")
    terminal_bullet(
        "providers[].models",
        "Models shown only after their provider is selected.",
    )
    terminal_status(
        "LINK",
        "Each custom provider ID maps to Codexier's internal route in config.toml.",
        "35",
        detail=codex_config,
    )
    terminal_status(
        "KEYS",
        "Do not put API keys in the provider-routing JSON file.",
        "33",
        detail="Keep credentials in the provider authentication configuration or environment.",
    )

    terminal_heading("After editing", "35")
    terminal_status(
        "RELOAD",
        "Use Codexier to sync changes, then close and reopen the model/provider menu.",
        "35",
        detail="No repatching or app restart is needed.",
    )

    if backup is not None:
        terminal_heading("Recovery", "34")
        terminal_status("BACKUP", "Complete original app backup:", "34", detail=backup)

    terminal_heading("Important", "33")
    terminal_status(
        "NOTICE",
        "The app now has an ad-hoc signature. A ChatGPT update may replace this patch.",
        "33",
    )
    print()


def fail(message: str, exit_code: int = 1) -> NoReturn:
    terminal_panel("Error", message, "31", stream=sys.stderr)
    raise SystemExit(exit_code)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    label: str | None = None,
    terminal: bool = True,
) -> subprocess.CompletedProcess[str]:
    if terminal:
        terminal_status(
            "STEP",
            label or f"Running {Path(command[0]).name}",
            "36",
            detail=shlex.join(command),
        )
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        output = exc.stdout.strip() if exc.stdout else ""
        if output and terminal:
            terminal_panel("Command output", output, "31", stream=sys.stderr)
        step = label or f"Running {Path(command[0]).name}"
        message = f"{step} failed with exit status {exc.returncode}."
        if output:
            message += f" Diagnostic output: {output[-2000:]}"
        else:
            message += (
                " The command produced no diagnostic output. Verify Node.js/npm, "
                "close Codex/ChatGPT and retry; if it exits unexpectedly on Windows, "
                "check Event Viewer → Windows Logs → Application."
            )
        raise PatchError(message) from exc


class FancyArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message: str, file: Any = None) -> None:
        if not message:
            return
        stream = file or sys.stdout
        width = terminal_width()
        title = " COMMAND HELP "
        top = f"╭─{title}{'─' * max(1, width - len(title) - 2)}"
        bottom = f"╰{'─' * (width - 1)}"
        print(file=stream)
        print(color(top, "1", "36", stream=stream), file=stream)
        for raw_line in message.rstrip().splitlines():
            border = color("│", "36", stream=stream)
            stripped = raw_line.strip()
            if not stripped:
                print(border, file=stream)
                continue
            if raw_line.startswith("usage:"):
                label, remainder = raw_line.split(":", 1)
                rendered = (
                    color(label.upper(), "1", "35", stream=stream)
                    + color(":", "35", stream=stream)
                    + color(remainder, "1", stream=stream)
                )
            elif stripped in {"options:", "optional arguments:"}:
                rendered = color(stripped.upper(), "1", "36", stream=stream)
            elif raw_line.startswith("  -"):
                option_and_help = re.split(r"(\s{2,})", stripped, maxsplit=1)
                option = option_and_help[0]
                remainder = "".join(option_and_help[1:])
                rendered = (
                    "  "
                    + color(option, "1", "33", stream=stream)
                    + color(remainder, stream=stream)
                )
            else:
                rendered = color(raw_line, stream=stream)
            print(f"{border} {rendered}", file=stream)
        print(color(bottom, "1", "36", stream=stream), file=stream)
        print(file=stream)
        stream.flush()

    def error(self, message: str) -> NoReturn:
        terminal_panel("Argument error", message, "31", stream=sys.stderr)
        terminal_status(
            "HELP",
            "Show all installer options with:",
            "33",
            detail=f"{self.prog} --help",
            stream=sys.stderr,
        )
        self.exit(2)


def invoking_user_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root" and pwd is not None:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


def parse_args() -> argparse.Namespace:
    home = invoking_user_home()
    configured_codex_home = os.environ.get("CODEX_HOME")
    codex_home = (
        Path(configured_codex_home).expanduser()
        if configured_codex_home
        else home / ".codex"
    )
    parser = FancyArgumentParser(
        description=(
            "Add a dynamic provider selector and per-model provider routing to the "
            "macOS ChatGPT/Codex desktop app."
        )
    )
    parser.add_argument(
        "--app",
        type=Path,
        default=Path("/Applications/ChatGPT.app"),
        help="ChatGPT.app to patch (default: /Applications/ChatGPT.app)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=codex_home / "desktop-model-providers.json",
        help="Provider-routing JSON file in the effective Codex home",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=home / "Applications" / "ChatGPT Patch Backups",
        help="Directory in which a complete app backup is created",
    )
    parser.add_argument(
        "--overwrite-config",
        action="store_true",
        help="Replace the provider-routing JSON with the built-in template",
    )
    return parser.parse_args()


def validate_provider_config(data: Any) -> None:
    if not isinstance(data, dict):
        raise PatchError("Provider config must be a JSON object")
    if data.get("version") != 2:
        raise PatchError("Provider config version must be 2")
    providers = data.get("providers")
    if not isinstance(providers, list) or not providers:
        raise PatchError("Provider config 'providers' must be a non-empty array")

    provider_ids: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            raise PatchError("Every provider must be an object")
        provider_id = provider.get("id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise PatchError("Every provider id must be a non-empty string")
        provider_id = provider_id.strip()
        if provider_id in provider_ids:
            raise PatchError(f"Duplicate provider id: {provider_id}")
        provider_ids.add(provider_id)
        label = provider.get("label")
        if not isinstance(label, str) or not label.strip():
            raise PatchError(f"Provider '{provider_id}' needs a non-empty label")
        description = provider.get("description", "")
        if not isinstance(description, str):
            raise PatchError(f"Provider '{provider_id}' description must be a string")
        models = provider.get("models")
        if not isinstance(models, list):
            raise PatchError(f"Provider '{provider_id}' models must be an array")
        model_ids: set[str] = set()
        for model in models:
            if not isinstance(model, dict):
                raise PatchError(
                    f"Every model for provider '{provider_id}' must be an object"
                )
            model_id = model.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise PatchError(
                    f"Every model for provider '{provider_id}' needs a non-empty id"
                )
            model_id = model_id.strip()
            if model_id in model_ids:
                raise PatchError(
                    f"Provider '{provider_id}' has a duplicate model id: {model_id}"
                )
            model_ids.add(model_id)
            label = model.get("label")
            if not isinstance(label, str) or not label.strip():
                raise PatchError(
                    f"Model '{model_id}' for provider '{provider_id}' "
                    "needs a non-empty label"
                )

    default_provider = data.get("default_provider")
    if default_provider not in provider_ids:
        raise PatchError("default_provider must reference a configured provider")

def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def ensure_provider_config(path: Path, overwrite: bool) -> str:
    if not path.exists() or path.stat().st_size == 0:
        raise PatchError(
            f"Provider routing config is missing: {path}. "
            "Sync at least one enabled provider before patching the desktop app."
        )
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PatchError(f"Cannot read valid JSON from {path}: {exc}") from exc
    validate_provider_config(data)
    return "kept"


def asar_header_hash(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            size_pickle = handle.read(8)
            if len(size_pickle) != 8:
                raise PatchError("ASAR archive is too short to contain a header")
            size_payload, header_pickle_size = struct.unpack("<II", size_pickle)
            if size_payload != 4 or header_pickle_size < 8:
                raise PatchError("ASAR archive has an invalid header-size pickle")

            header_pickle = handle.read(header_pickle_size)
            if len(header_pickle) != header_pickle_size:
                raise PatchError("ASAR archive contains a truncated header")
    except OSError as exc:
        raise PatchError(f"Cannot read ASAR header from {path}: {exc}") from exc

    header_payload_size, header_string_size = struct.unpack("<II", header_pickle[:8])
    if header_payload_size > header_pickle_size - 4:
        raise PatchError("ASAR header payload size is invalid")
    header_start = 8
    header_end = header_start + header_string_size
    if header_end > len(header_pickle):
        raise PatchError("ASAR header string is truncated")

    header_json = header_pickle[header_start:header_end]
    try:
        json.loads(header_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PatchError("ASAR header does not contain valid UTF-8 JSON") from exc
    return hashlib.sha256(header_json).hexdigest()


def contains_marker(path: Path, marker: bytes = PATCH_MARKER) -> bool:
    overlap = len(marker) - 1
    previous = b""
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            data = previous + chunk
            if marker in data:
                return True
            previous = data[-overlap:] if overlap else b""
    return False


def contains_legacy_marker(path: Path) -> bool:
    return any(contains_marker(path, marker) for marker in LEGACY_PATCH_MARKERS)


def load_plist(path: Path) -> tuple[dict[str, Any], plistlib.PlistFormat]:
    raw = path.read_bytes()
    plist_format = plistlib.FMT_BINARY if raw.startswith(b"bplist00") else plistlib.FMT_XML
    try:
        data = plistlib.loads(raw)
    except Exception as exc:
        raise PatchError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PatchError(f"Unexpected plist root in {path}")
    return data, plist_format


def asar_integrity_hash(plist: dict[str, Any]) -> str:
    try:
        value = plist["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"]
    except (KeyError, TypeError) as exc:
        raise PatchError("Info.plist has no Electron ASAR integrity entry") from exc
    if not isinstance(value, str):
        raise PatchError("Electron ASAR integrity hash is not a string")
    return value.lower()


def app_path_variants(app: Path) -> set[str]:
    variants = {str(app), str(app.resolve())}
    for value in tuple(variants):
        if value.startswith("/private/tmp/") or value.startswith("/private/var/"):
            variants.add(value[len("/private") :])
        elif value.startswith("/tmp/") or value.startswith("/var/"):
            variants.add(f"/private{value}")
    return variants


def find_target_app_processes(app: Path) -> list[tuple[int, str]]:
    prefixes = tuple(f"{variant.rstrip('/')}/" for variant in app_path_variants(app))
    try:
        result = subprocess.run(
            ["/bin/ps", "-ww", "-axo", "pid=,command="],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PatchError(f"Could not inspect running processes: {exc}") from exc

    matches: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parsed = re.match(r"\s*(\d+)\s+(.+)", line)
        if parsed is None:
            continue
        pid = int(parsed.group(1))
        command = parsed.group(2)
        if pid != os.getpid() and command.startswith(prefixes):
            matches.append((pid, command))
    return matches


def wait_for_app_processes_to_exit(app: Path, timeout: float) -> list[tuple[int, str]]:
    deadline = time.monotonic() + timeout
    remaining = find_target_app_processes(app)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.2)
        remaining = find_target_app_processes(app)
    return remaining


def stop_target_app_processes(app: Path, allow_running: bool) -> None:
    """Compatibility wrapper that no longer force-closes desktop processes."""
    if allow_running:
        raise PatchSkipped(
            "Patching while ChatGPT is running is no longer supported. "
            "Close it normally, then run the patch again."
        )
    gracefully_close_target_app_processes(app)


def gracefully_close_target_app_processes(app: Path, *, force: bool = False) -> bool:
    """Ask the app to quit and skip safely if it remains open.

    Unlike the standalone legacy installer, automatic sync never sends a
    signal or force-kills a desktop process.  This protects unsaved work and
    keeps the app archive untouched when a graceful close is not possible.
    """
    processes = find_target_app_processes(app)
    if not processes:
        terminal_status("PROCESS", "The target ChatGPT app is not running.", "32", detail=app)
        return False

    pid_summary = ", ".join(str(pid) for pid, _command in processes)
    terminal_status(
        "CLOSE",
        "Requesting a graceful close of the target ChatGPT app.",
        "35",
        detail=f"PIDs: {pid_summary}",
    )
    escaped = str(app).replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                f'tell application (POSIX file "{escaped}" as alias) to quit',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise PatchSkipped(
            f"Desktop patch skipped: could not request a graceful app close ({exc}). "
            "Close ChatGPT manually, then sync enabled providers again."
        ) from exc

    remaining = wait_for_app_processes_to_exit(app, 8.0)
    if remaining:
        details = ", ".join(str(pid) for pid, _command in remaining)
        if force:
            terminal_status(
                "FORCE CLOSE",
                "Forcefully terminating the remaining ChatGPT processes.",
                "31",
                detail=f"PIDs: {details}",
            )
            for pid, _command in remaining:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                except OSError as exc:
                    raise PatchSkipped(
                        f"Desktop patch skipped: could not force-close PID {pid} ({exc})."
                    ) from exc
            remaining = wait_for_app_processes_to_exit(app, 3.0)
            if not remaining:
                terminal_status("CLOSED", "The target ChatGPT app was forcefully closed.", "32")
                return True
        raise PatchSkipped(
            "Desktop patch skipped: ChatGPT is still running "
            f"(PIDs: {details}). Close it normally, then sync enabled providers again."
        )
    terminal_status("CLOSED", "The target ChatGPT app closed gracefully.", "32")
    return True


def unique_candidate(
    assets: Path,
    content_needles: tuple[str, ...],
    role: str,
) -> Path:
    candidates = sorted(
        path
        for path in assets.glob("*.js")
        if not path.name.endswith(".map.js")
    )
    matches = []
    for path in candidates:
        source = path.read_text(encoding="utf-8")
        if all(needle in source for needle in content_needles):
            matches.append(path)
    if len(matches) != 1:
        raise PatchError(
            f"Expected exactly one {role} JavaScript bundle containing all "
            f"required source markers, found {len(matches)} among "
            f"{len(candidates)} JavaScript bundles"
        )
    return matches[0]


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
            "}=e,P=m", "}=e;p=codexUseProviderModels(p);let P=m"
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
                "let z=R,B=A===void 0?!1:A,V=j===void 0?!0:j,H=M===void 0?!1:M,U=ed(),",
                "p=codexUseProviderModels(p);let z=R,B=A===void 0?!1:A,V=j===void 0?!0:j,H=M===void 0?!1:M,U=ed(),",
                CODEX_26721_5848_LAYOUT,
            )
            source = _replace_once(
                source,
                "children:[m,(0,wQ.jsx)(`div`,{className:`vertical-scroll-fade-mask",
                "children:[(0,wQ.jsx)(CodexCustomProviderPickerSection,{}),m,(0,wQ.jsx)(`div`,{className:`vertical-scroll-fade-mask",
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


def make_backup(
    app: Path, backup_dir: Path, version: str, build: str, *, automatic: bool = False
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    if automatic:
        for previous in backup_dir.glob("automatic-ChatGPT-*.app"):
            if previous.is_dir() and not previous.is_symlink():
                shutil.rmtree(previous)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version)
    safe_build = re.sub(r"[^A-Za-z0-9._-]+", "-", build)
    prefix = "automatic-" if automatic else ""
    backup = backup_dir / (
        f"{prefix}ChatGPT-{safe_version}-build-{safe_build}-{timestamp}.app"
    )
    suffix = 1
    while backup.exists():
        backup = backup_dir / (
            f"ChatGPT-{safe_version}-build-{safe_build}-{timestamp}-{suffix}.app"
        )
        suffix += 1
    run(
        ["/usr/bin/ditto", str(app), str(backup)],
        label="Creating a complete app backup",
    )
    if not (backup / "Contents" / "Resources" / "app.asar").is_file():
        raise PatchError(f"Backup verification failed: {backup}")
    return backup


def atomic_replace_file(source: Path, target: Path) -> None:
    original_stat = target.stat()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.patch-", dir=target.parent)
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary_path)
        os.chmod(temporary_path, original_stat.st_mode)
        if os.geteuid() == 0:
            os.chown(temporary_path, original_stat.st_uid, original_stat.st_gid)
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def restore_backup(app: Path, backup: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    failed_copy = app.with_name(f"{app.stem}.patch-failed-{timestamp}.app")
    suffix = 1
    while failed_copy.exists():
        failed_copy = app.with_name(
            f"{app.stem}.patch-failed-{timestamp}-{suffix}.app"
        )
        suffix += 1
    os.replace(app, failed_copy)
    try:
        run(
            ["/usr/bin/ditto", str(backup), str(app)],
            label="Restoring the original app from backup",
        )
    except Exception:
        os.replace(failed_copy, app)
        raise
    return failed_copy


def latest_original_backup(app: Path, backup_dir: Path) -> Path:
    """Find the newest verified, unpatched backup for this app build."""
    info, _ = load_plist(app / "Contents" / "Info.plist")
    version = re.sub(r"[^A-Za-z0-9._-]+", "-", str(info.get("CFBundleShortVersionString", "unknown")))
    build = re.sub(r"[^A-Za-z0-9._-]+", "-", str(info.get("CFBundleVersion", "unknown")))
    prefix = f"ChatGPT-{version}-build-{build}-"
    backups = sorted(
        (path for path in backup_dir.glob(f"{prefix}*.app") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for backup in backups:
        archive = backup / "Contents" / "Resources" / "app.asar"
        if archive.is_file() and not contains_marker(archive):
            return backup
    raise PatchError(
        f"No verified original backup for ChatGPT {version}, build {build} was found in {backup_dir}."
    )


def restore_original_app(app: Path, backup: Path, progress: PatchProgress | None = None) -> Path:
    """Restore a full verified app backup without touching Codex configuration or sessions."""
    backup_asar = backup / "Contents" / "Resources" / "app.asar"
    backup_info = backup / "Contents" / "Info.plist"
    if not backup_asar.is_file() or not backup_info.is_file() or contains_marker(backup_asar):
        raise PatchError(f"Backup is not a verified original ChatGPT app: {backup}")
    report(progress, "process stop", "closing the patched desktop app before restore")
    gracefully_close_target_app_processes(app)
    report(progress, "atomic replacement", "restoring the original desktop app from backup")
    failed_copy = restore_backup(app, backup)
    restored_asar = app / "Contents" / "Resources" / "app.asar"
    restored_info = app / "Contents" / "Info.plist"
    report(progress, "verification", "verifying the restored original desktop app")
    if (
        not restored_asar.is_file()
        or restored_asar.read_bytes() != backup_asar.read_bytes()
        or not restored_info.is_file()
        or restored_info.read_bytes() != backup_info.read_bytes()
        or contains_marker(restored_asar)
    ):
        raise PatchError(f"Restored app does not match the verified original backup: {backup}")
    return failed_copy


def restore_archive_backup(
    app: Path, backup: Path, progress: PatchProgress | None = None
) -> None:
    """Restore an immutable app.asar.bak and re-sign its matching app bundle."""
    asar_path = app / "Contents" / "Resources" / "app.asar"
    info_path = app / "Contents" / "Info.plist"
    if not backup.is_file() or contains_marker(backup) or contains_legacy_marker(backup):
        raise PatchError(f"Backup is not an original app.asar archive: {backup}")
    original_asar = asar_path.read_bytes()
    original_info = info_path.read_bytes()
    report(progress, "process stop", "closing the desktop app before restore")
    gracefully_close_target_app_processes(app)
    try:
        report(progress, "atomic replacement", "restoring app.asar from immutable backup")
        atomic_replace_file(backup, asar_path)
        info, plist_format = load_plist(info_path)
        info["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = asar_header_hash(backup)
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            plistlib.dump(info, temporary, fmt=plist_format, sort_keys=False)
            plist_path = Path(temporary.name)
        try:
            atomic_replace_file(plist_path, info_path)
        finally:
            plist_path.unlink(missing_ok=True)
        run(
            ["/usr/bin/codesign", "--deep", "--force", "--sign", "-", str(app)],
            label="Applying the ad-hoc app signature",
        )
        report(progress, "verification", "verifying restored archive and application signature")
        final_info, _ = load_plist(info_path)
        if (
            asar_path.read_bytes() != backup.read_bytes()
            or asar_header_hash(asar_path) != asar_integrity_hash(final_info)
            or contains_marker(asar_path)
            or contains_legacy_marker(asar_path)
        ):
            raise PatchError("Restored app does not match its immutable original backup")
    except Exception:
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            temporary.write(original_asar)
            asar_restore = Path(temporary.name)
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            temporary.write(original_info)
            info_restore = Path(temporary.name)
        try:
            atomic_replace_file(asar_restore, asar_path)
            atomic_replace_file(info_restore, info_path)
        finally:
            asar_restore.unlink(missing_ok=True)
            info_restore.unlink(missing_ok=True)
        raise


@contextmanager
def restore_app_after_failure(
    app: Path, backup: Path, progress: PatchProgress | None
) -> Any:
    """Restore and verify the full app bundle after every post-backup failure."""
    try:
        yield
    except Exception:
        report(progress, "atomic replacement", "rolling back from the verified backup")
        terminal_status(
            "RECOVERY",
            "Patching failed after backup. Restoring the original app.",
            "33",
            stream=sys.stderr,
        )
        try:
            failed_copy = restore_backup(app, backup)
            report(progress, "verification", "verifying the restored original application")
            backup_asar = backup / "Contents" / "Resources" / "app.asar"
            restored_asar = app / "Contents" / "Resources" / "app.asar"
            if backup_asar.read_bytes() != restored_asar.read_bytes():
                raise PatchError("Restored app.asar does not match the verified backup")
            terminal_status(
                "RESTORED",
                "The original app was restored. The failed patched copy was retained.",
                "32",
                detail=failed_copy,
                stream=sys.stderr,
            )
        except Exception as restore_exc:
            terminal_panel(
                "Recovery failed",
                f"Automatic restoration failed: {restore_exc}\n"
                f"The full backup remains at: {backup}",
                "31",
                stream=sys.stderr,
            )
            raise PatchError(
                f"Patch failed and automatic restoration failed; backup remains at: {backup}"
            ) from restore_exc
        raise


def patch_app(
    app: Path,
    config: Path,
    backup_dir: Path,
    overwrite_config: bool,
    progress: PatchProgress | None = None,
) -> None:
    info_path = app / "Contents" / "Info.plist"
    resources = app / "Contents" / "Resources"
    asar_path = resources / "app.asar"
    unpacked_path = resources / "app.asar.unpacked"

    if sys.platform != "darwin":
        raise PatchError("This installer only supports macOS")
    if not app.is_dir() or not info_path.is_file() or not asar_path.is_file():
        raise PatchError(f"Not a supported ChatGPT app bundle: {app}")
    if not unpacked_path.is_dir():
        raise PatchError(f"Missing ASAR companion directory: {unpacked_path}")
    if shutil.which("npx") is None:
        raise PatchError("npx is required. Install Node.js, then run this installer again")

    config_action = ensure_provider_config(config, overwrite_config)
    terminal_status(
        "CONFIG",
        "Provider-routing config created."
        if config_action == "written"
        else "Existing provider-routing config validated.",
        "36",
        detail=config,
    )

    info, plist_format = load_plist(info_path)
    version = str(info.get("CFBundleShortVersionString", "unknown"))
    build = str(info.get("CFBundleVersion", "unknown"))
    sidecar = asar_path.with_name(f"{asar_path.name}.bak")
    if contains_marker(asar_path) or contains_legacy_marker(asar_path):
        if not sidecar.is_file() or contains_marker(sidecar) or contains_legacy_marker(sidecar):
            raise PatchError(
                "Cannot apply new patches to an already patched app without a "
                f"verified immutable original backup at: {sidecar}"
            )
        terminal_status(
            "RESTORE",
            "Restoring the immutable original app before applying new patches.",
            "34",
            detail=sidecar,
        )
        restore_archive_backup(app, sidecar, progress)
        info, plist_format = load_plist(info_path)

    sidecar, created = immutable_file_backup(asar_path)
    if contains_marker(sidecar) or contains_legacy_marker(sidecar):
        raise PatchError(f"Immutable backup is patched and cannot be used: {sidecar}")
    asar_header_hash(sidecar)
    report(
        progress,
        "backup",
        f"{'created' if created else 'reused'} immutable original backup: {sidecar}",
    )

    current_header_hash = asar_header_hash(asar_path)
    expected_header_hash = asar_integrity_hash(info)
    if current_header_hash != expected_header_hash:
        raise PatchError(
            "The ASAR header does not match the current app's Info.plist integrity "
            "metadata. The bundle may be incomplete or modified."
        )
    terminal_status(
        "VERIFY",
        "The original app's ASAR header integrity is valid.",
        "32",
        detail=current_header_hash,
    )

    terminal_heading("Installation", "35")
    terminal_status(
        "APP",
        f"Preparing ChatGPT {version}, build {build}.",
        "34",
        detail=app,
    )
    report(progress, "backup", "creating a verified backup of the desktop app")
    backup = make_backup(app, backup_dir, version, build, automatic=True)
    terminal_status("OK", "App backup created.", "32", detail=backup)
    with restore_app_after_failure(
        app, backup, progress
    ), tempfile.TemporaryDirectory(prefix="chatgpt-provider-patch-") as temporary:
        work = Path(temporary)
        extracted = work / "app"
        patched_asar = work / "app.asar"
        patched_plist = work / "Info.plist"

        report(progress, "extraction", "extracting application resources")
        run(
            ["npx", "--yes", ASAR_PACKAGE, "extract", str(asar_path), str(extracted)],
            label="Extracting application resources",
        )
        assets = extracted / "webview" / "assets"
        if not assets.is_dir():
            raise PatchError("Extracted app has no webview/assets directory")

        report(progress, "bundle matching", "matching the supported application bundle layout")
        central = unique_candidate(
            assets,
            ("async prewarmThreadStart(",),
            "App Server client",
        )
        picker = unique_candidate(
            assets,
            ("modelOptionsDisabled:m",),
            "model picker",
        )

        report(progress, "source patch", "applying provider-first model routing")
        patch_layout = apply_supported_patch_variant(central, picker)
        terminal_status(
            "LAYOUT",
            "Matched a supported application bundle layout.",
            "32",
            detail=patch_layout,
        )

        if PATCH_MARKER.decode() not in central.read_text(encoding="utf-8"):
            raise PatchError("Routing marker missing after patch")
        if "CodexCustomProviderPickerSection" not in picker.read_text(encoding="utf-8"):
            raise PatchError("Provider picker missing after patch")

        report(progress, "repack", "repacking the patched application archive")
        run(
            ["npx", "--yes", ASAR_PACKAGE, "pack", str(extracted), str(patched_asar)],
            label="Packing patched application resources",
        )

        if not contains_marker(patched_asar):
            raise PatchError("Packed ASAR does not contain the patch marker")
        patched_header_hash = asar_header_hash(patched_asar)
        info["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = patched_header_hash
        with patched_plist.open("wb") as handle:
            plistlib.dump(info, handle, fmt=plist_format, sort_keys=False)

        try:
            report(progress, "atomic replacement", "atomically replacing the application archive")
            atomic_replace_file(patched_asar, asar_path)
            atomic_replace_file(patched_plist, info_path)
            run(
                ["/usr/bin/codesign", "--deep", "--force", "--sign", "-", str(app)],
                label="Applying the ad-hoc app signature",
            )
            run(
                [
                    "/usr/bin/codesign",
                    "--verify",
                    "--deep",
                    "--strict",
                    "--verbose=2",
                    str(app),
                ],
                label="Verifying the app signature",
            )

            report(progress, "verification", "verifying the installed archive and application signature")
            final_info, _ = load_plist(info_path)
            if asar_header_hash(asar_path) != asar_integrity_hash(final_info):
                raise PatchError("Installed ASAR integrity verification failed")
            if not contains_marker(asar_path):
                raise PatchError("Installed ASAR is missing the patch marker")
        except Exception:
            raise

    print_completion_summary(config, backup=backup, upgraded=False)


def main() -> int:
    args = parse_args()
    try:
        app = args.app.expanduser().resolve()
        gracefully_close_target_app_processes(app)
        patch_app(
            app,
            args.config.expanduser().resolve(),
            args.backup_dir.expanduser().resolve(),
            args.overwrite_config,
        )
    except PatchError as exc:
        fail(str(exc))
    except PermissionError as exc:
        fail(f"Permission denied: {exc}")
    except KeyboardInterrupt:
        fail("Interrupted", 130)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
