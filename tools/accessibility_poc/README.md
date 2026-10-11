# macOS Accessibility POC

This is a read-only developer POC for the currently visible QQ or WeChat window. It uses only Apple `NSWorkspace` and Accessibility (`AXUIElement`) APIs. It does not scroll, click, type, attach a debugger, inspect process memory, read databases or keys, inject code, alter either client, or change macOS protections.

Build and run the content-free self-test:

```sh
xcrun clang -fobjc-arc -fmodules-cache-path=/tmp/chat-poc-module-cache tools/accessibility_poc/main.m -framework AppKit -framework ApplicationServices -o /tmp/chat-accessibility-poc
/tmp/chat-accessibility-poc --self-test
```

The self-test may detect whether the applications are installed/running and their current bundle version. It never creates an AX application element for QQ or WeChat and never reads their windows.

Target inspection is deliberately gated. It examines only the focused, already-visible window, performs no scrolling, and prints structural classifications by default:

```sh
/tmp/chat-accessibility-poc --target qq --acknowledge-visible-content
/tmp/chat-accessibility-poc --target wechat --acknowledge-visible-content
```

The command never serializes or prints captured Accessibility strings. It does not request full `AXValue`, title, description, text-area, or text-field values. For a framed static-text element intersecting the focused window it only requests the Accessibility-provided visible character range through the bounded string-for-range API; unsupported ranges fail closed. The requested range is capped at 4,096 characters, aggregate retained text at 1 MiB, and the child queue as well as total nodes are also capped. If no accessible text candidates are found, stop and classify the route as NO-GO; do not switch to memory access, database-key extraction, injection, client modification, or disabling SIP.

Candidate text alone is reported as `UNVERIFIED`: application chrome can also be exposed as text, and a stable AX tree shape is not proof of stable message order. A human Gate 2 review must compare a dedicated non-sensitive test conversation against the visible UI before claiming message text, sender, timestamp, or order support. Process matching uses the exact installed QQ and WeChat bundle identifiers rather than application display names.
