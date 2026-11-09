import SwiftUI

/// The animated bubble. Observes only `BubbleModel`, so conversation or streaming-text updates
/// elsewhere never invalidate it.
struct BubbleView: View {
    @ObservedObject var model: BubbleModel
    var mini = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let reduce = reduceMotion || LokiDesign.Motion.reduceMotion
        var style = BubbleStyle.preset(for: model.phase)
            .adapted(mini: mini, emphasis: reduce ? 0 : model.emphasis)
        if reduce { style = style.reducedMotion() }

        return BubbleCanvas(
            style: style,
            meter: model.meter,
            burstStart: model.burstStart,
            interval: model.phase.renderInterval,
            // Reduce Motion freezes the shapes; the timeline only runs to follow a real microphone.
            paused: !model.isRendering || (reduce && model.phase != .listening),
            frozen: reduce
        )
        .animation(reduce ? LokiDesign.Motion.reduced : LokiDesign.Motion.stateChange, value: model.phase)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// `Animatable` so one spring interpolates the whole style vector (colors included) on every state
/// change. The timeline inside keeps its own clock, so retargeting never restarts the motion.
struct BubbleCanvas: View, Animatable {
    var style: BubbleStyle
    let meter: AudioMeter
    let burstStart: TimeInterval?
    let interval: TimeInterval
    let paused: Bool
    let frozen: Bool

    var animatableData: BubbleStyle {
        get { style }
        set { style = newValue }
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: interval, paused: paused)) { timeline in
            Canvas(rendersAsynchronously: false) { context, size in
                let now = timeline.date.timeIntervalSinceReferenceDate
                let level = meter.sample(at: now)
                let t = frozen ? 7.3 : now

                var burst: Double?
                if let burstStart {
                    let p = (now - burstStart) / LokiDesign.Motion.burstDuration
                    if p >= 0, p < 1 { burst = p }
                }

                var ctx = context
                BubbleRenderer.draw(into: &ctx, size: size, style: style, time: t, level: level, burst: burst)
            }
        }
    }
}
