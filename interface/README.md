# Loki macOS Interface (`loki-macos`)

Loki's primary desktop interface is a lightweight, native macOS Swift/SwiftUI floating assistant (`interface/loki-macos`) that replaces the heavier Tauri/WebView stack. Its visual identity is **Liquid Aurora**: a living, luminous Bubble that shows what the assistant is doing.

## Features
- **Liquid Aurora Bubble**: a borderless, non-activating `NSPanel` anchored at the bottom-center of the screen (freely draggable) that never steals focus from the app Loki is automating. The Bubble is drawn with SwiftUI `Canvas` (roaming gradient fields, organic outline deformation, luminous core, halo, specular sheen) and animates between states with one spring over a single style vector, so transitions never reset or jump.
- **State-driven animation** (driven by the real assistant lifecycle):
  | State | Trigger | Look |
  | --- | --- | --- |
  | Idle | nothing in flight | soft blue-violet, slow breathing |
  | Listening | recording | cyan/electric blue, rings that follow the real microphone RMS (`AVAudioEngine`) |
  | Thinking | job running, no action yet | indigo/violet, internal swirl, bright core |
  | Executing | backend has reported an automation step | focused cyan glow, light sweep, controlled pulses; caption shows the backend's real step text |
  | Responding | speaking a reply / revealing a text reply | most expressive: blue, cyan, violet, magenta accents, deformation, aura |
  | Awaiting reply | backend asked a follow-up question | calm rings |
  | Completed | job finished OK | brief mint highlight + ring, then back to idle |
  | Error | request/job failed | restrained ember accent; full error kept in the conversation; self-recovers |
- **Compact caption**: a small glass capsule pairs a glyph with the state text (never color alone).
- **Expanded conversation**: `Cmd+Shift+Space`, hover button, or right-click → *Open Conversation*. The Bubble travels into the header as an avatar. Includes markdown (inline, headings, bullets, fenced code with copy), real backend plan/step progress, an error card with preserved details, a voice button, and a send button.
- **Global Shortcuts**:
  - `Cmd+Shift+V` (or click the Bubble): start/stop voice recording (also barges in and stops active speech).
  - `Cmd+Shift+Space`: open/close the conversation. It also opens automatically when Loki asks a follow-up question. `Esc` collapses.
- **Main menu**: Loki installs a standard macOS main menu (App, Edit, Conversation, Window, Help). The menu bar is hidden because Loki is an accessory app, but the menu routes key equivalents while the conversation is the key window, so `Cmd+Z`, `Shift+Cmd+Z`, `Cmd+X`, `Cmd+C`, `Cmd+V` and `Cmd+A` work in the input field.
- **In-app shortcuts** (active while the conversation has keyboard focus):
  | Shortcut | Action |
  | --- | --- |
  | `Cmd+,` | Open Settings |
  | `Cmd+W` | Close the conversation |
  | `Cmd+K` | Clear the conversation |
  | `Cmd+L` | Focus the input field (opens the conversation if needed) |
  | `Cmd+.` | Stop speaking |
  | `Cmd+Q` | Quit |
- **Status item**: a menu bar icon (template image, so it adapts to the menu bar appearance) whose symbol follows the assistant state: idle, listening, working, responding, waiting for a reply, done, error. Its tooltip and VoiceOver value carry the current status text. The menu shows the status line, Start/Stop Listening, Open/Close Conversation, Stop Speaking, Show/Hide Bubble, Reset Orb Position, Settings…, About Loki and Quit. Items enable and retitle according to state, and shortcuts are real key equivalents. Hiding the bubble keeps the global hotkeys working; `Cmd+Shift+Space` (or a follow-up question) shows it again.
- **Settings** (`Cmd+,`, or *Settings…* in the status item, app menu or gear menu): one reusable native window that follows the system appearance and remembers its position. Preferences are stored in `UserDefaults` with a `loki.` key prefix (domain `LokiOrb` when run unbundled with `swift run`, the bundle identifier inside an app bundle).
  | Tab | Contents |
  | --- | --- |
  | General | *Show the Loki bubble when the app starts* (`loki.showBubbleAtLaunch`, default on; choosing Hide/Show Bubble from a menu updates it, and when off the status item and `Cmd+Shift+Space` bring the bubble back). *Play a sound when listening starts and stops* (`loki.playListeningSounds`, default on). *Open the conversation when Loki asks a question* (`loki.openConversationForQuestions`, default on; when off the bubble stays compact, its caption says Loki has a question, and the question remains in the conversation and is still spoken). *Open on the display where the pointer is* (`loki.summonOnActiveDisplay`, default on). |
  | General (Appearance) | *Conversation appearance*: Match System (default), Light or Dark (`loki.appearance`). |
  | Shortcuts | The two global shortcuts and the in-app shortcuts. Each global shortcut shows **Active**, or **Unavailable: another app already uses this shortcut** when macOS refused the registration. |
  | Accessibility | *When VoiceOver is running*: Let VoiceOver read replies (default) or Use Loki voice as well (`loki.spokenRepliesWithVoiceOver`). |
  | About | Version, server address, and a *Check Server* button (read-only `GET /health`) that reports Reachable or Unreachable. |
- **Listening cues**: with the sound preference on, the system sounds *Tink* (start) and *Pop* (stop) play quietly. The first moments of capture are discarded after the start cue, so the cue is never sent to the server as speech. This gives listening an audible state in addition to the visible one.
- **Focus return**: when the conversation takes keyboard focus, Loki remembers the app that was frontmost, and refreshes it when the conversation is focused again from another app (Ask Loki, a drop, Focus Input). `Esc`, `Cmd+W` or the collapse button hands focus back to that app, unless you have already switched to another one. A pending follow-up question keeps focus.
- **Single instance**: starting a second copy of Loki exits immediately with a one-line message on stderr, so there is never a duplicate status item or a failed second hotkey registration.
- **Spaces**: the panel joins all Spaces, stays above full-screen apps, and is skipped by ``Cmd+` `` window cycling and the Window menu.
- **Position memory**: where you drag the panel is remembered across launches (`loki.panelPosition`: the panel's bottom-center point as a fraction of the screen's visible width and points above its bottom, plus the display's `NSScreenNumber`). If that display is not attached at launch, Loki uses bottom-center of the main screen. *Reset Orb Position* (status item, right-click menu, gear menu) returns to bottom-center and clears the remembered position, so later launches also start there.
- **Displays and screen changes**: when displays are attached or removed, the resolution changes, or the Dock or menu bar auto-hides, the panel is moved back inside the visible area of the nearest screen. Expanding and collapsing still keep the bubble's bottom-center fixed. With *Open on the display where the pointer is* (Settings > General, `loki.summonOnActiveDisplay`, default on), `Cmd+Shift+Space` (when opening) and `Cmd+Shift+V` (when starting) move the panel to the same relative position on the display holding the pointer. Clicking the bubble, the menus, and closing or stopping never move it.
- **Drag and drop**: drop text or files on the bubble or the open conversation. An accent-colored ring shows the drop target. Text is added to the input field (after any text already there, on one line); files add their paths, quoted when they contain spaces or special characters, so you can type what Loki should do with them. The conversation opens with the field focused. Nothing is sent automatically: you press Return.
- **Appearance**: the conversation follows the system Light or Dark appearance (colors are dynamic and resolve per appearance; the bubble itself floats over any desktop and looks the same). The Appearance setting can force Light or Dark through the panel's `NSAppearance`. Text uses system text styles (`.body`, `.subheadline`, ...).
- **Display accessibility settings**: *Increase Contrast* gives stronger hairlines, higher-opacity secondary text, a solid card outline and a 2 pt input focus ring. *Reduce Transparency* replaces the glass materials with solid window-background fills. *Differentiate Without Color* adds "You", "Loki" and "Error" labels with icons, a thicker accent bar and a dashed error outline, so speakers and errors are not told apart by tint alone. *Reduce Motion* gives static shapes, no deformation and restrained transitions.
- **VoiceOver**: labels, values and actions throughout (the compact bubble is a single button with value and named actions; the conversation is a labelled list of messages; the input field, mic, send, details and copy controls all have labels). While VoiceOver is running Loki posts announcements for: listening started and stopped, executing steps (at most one every 2 seconds, never the same text twice in a row), follow-up questions, replies, errors, and "Done" when a command finishes without a reply. Every announcement is also in the conversation or status line as text.
- **VoiceOver and Loki's voice**: by default Loki does not speak replies, questions or errors aloud while VoiceOver is running, so two voices never overlap; VoiceOver announces them instead. Set *Use Loki voice as well* in Settings > Accessibility to hear both. Without VoiceOver nothing changes. After the "Listening" announcement the first 0.9 s of capture is discarded so the announcement is not recorded.
- **Keyboard and other accessibility**: all controls are keyboard-operable; the input field takes focus when the conversation opens.
- **Loki.app**: `make app` packages Loki as a real app bundle (`.build/Loki.app`, bundle identifier `com.loki.assistant`, no Dock icon because it is a menu bar and floating-panel app). The icon is the Liquid Aurora bubble, drawn by the app's own renderer at build time. As an app, macOS asks for microphone access in Loki's name instead of the terminal's, and Loki can use Login Items and the Services menu.
- **Launch at Login**: *Settings > General > Launch Loki at login* registers the app with macOS (`SMAppService`). The system owns the setting and nothing is stored in `UserDefaults`; the toggle follows what System Settings > General > Login Items shows. If macOS asks for approval, a button opens Login Items. Unbundled (`swift run`) the toggle is disabled and reads "Available when running Loki.app".
- **Ask Loki service**: select text in any app, then choose *Services > Ask Loki* (or the same entry in the right-click menu). Loki comes to the display you are working on, opens the conversation and adds the text to the input field, on one line, after any text already there. Nothing is sent until you press Return. The service exists only in Loki.app; if it does not appear at first, log out and in once, or enable it in System Settings > Keyboard > Keyboard Shortcuts > Services > Text.
- **Performance**: idle renders at 24 fps, quiet states stay low-rate, drawing pauses when the panel is occluded or the display sleeps, and the high-frequency audio level bypasses SwiftUI observation.

## Layout

```
Sources/LokiOrb/
  App/         AppBundle (bundled or not), AppIcon (icon rendering and `--export-icon`), LaunchAtLogin (SMAppService), ServicesProvider (Ask Loki), FocusReturn, MainMenu, AppCommands (shared menu target + validation), StatusItemController, AboutPanel, SingleInstanceGuard
  Settings/    LokiSettings (persisted preferences), SettingsView, SettingsWindowController
  Accessibility/ AccessibilityAnnouncer (VoiceOver announcements)
  Design/      LokiDesign.swift (tokens), LocalFlag.swift
  Bubble/      BubbleModel (phase, audio meter), BubbleStyle (animatable look), BubbleRenderer (Canvas), BubbleView
  Interface/   AssistantRootView (compact + transition), ExpandedCardView (header, input), ConversationView, DropTarget (drag and drop)
  LokiController.swift  lifecycle + backend polling      OrchestrationClient.swift  HTTP client
  FloatingOrbPanel.swift  PanelPlacement.swift (frame clamping, relative position math)  LokiApp.swift  HotkeyManager.swift  AudioCaptureService.swift  SpeechOutputService.swift
```

## Running

1. Start the Python orchestration server:
```bash
cd orchestration
uv run python server.py
```

2. Build and run the native macOS Swift app:
```bash
cd interface/loki-macos
swift run
```

## Building Loki.app

```bash
make app            # from the repository root; builds .build/Loki.app (release, ad-hoc signed)
make open-app       # build, then open it
# or: interface/loki-macos/scripts/bundle.sh
```

`scripts/bundle.sh` runs `swift build -c release`, copies the binary and `Resources/Info.plist` into `Loki.app`, generates `AppIcon.icns` (the app renders its own icon with `LokiOrb --export-icon <dir>`, and `iconutil` packs it), sets the build number from the commit count, and signs with `codesign --force --deep --sign -`. Set `SWIFT_BUILD_FLAGS` to pass extra flags to `swift build` (for example `--disable-sandbox` in a restricted environment).

Check it with `codesign --verify --deep --strict interface/loki-macos/.build/Loki.app` and `plutil -lint interface/loki-macos/.build/Loki.app/Contents/Info.plist`.

- **First launch check (not yet verified)**: the bundled app could not be launched in the restricted environment where it was built, so run `make open-app` once in a normal login session and confirm four things: the bubble appears, the status item appears in the menu bar, starting a second copy exits at once (single-instance guard), and the first voice input shows the microphone permission prompt in Loki's name.
- **Microphone permission**: the first time you start voice input, macOS asks whether Loki may use the microphone, with the text from `NSMicrophoneUsageDescription`. Change it later in System Settings > Privacy & Security > Microphone.
- **Server address**: an app opened from Finder or `open` does not inherit shell variables, so it uses the default `http://127.0.0.1:9820`. Start the server with `make server` (default port).
- **Ad-hoc signature**: the build has no Developer ID and is not notarized. It runs on the Mac that built it. On another Mac, Gatekeeper blocks it at first: right-click Loki.app and choose Open, then confirm. Rebuilding changes the signature, so macOS may ask again for microphone access and Login Items approval.
- **Unbundled**: `swift run` still works as before. Microphone access is then attributed to the terminal, there is no custom icon in the Dock or app switcher beyond the runtime icon set for the About panel, and Launch at Login and the Ask Loki service are unavailable.
