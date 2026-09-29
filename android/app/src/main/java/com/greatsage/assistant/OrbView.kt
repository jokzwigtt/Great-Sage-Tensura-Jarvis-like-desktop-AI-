package com.greatsage.assistant

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.Shader
import android.view.View
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * The Great Sage orb: a small intense core inside thin octagon rings and a slowly rotating wireframe.
 * The core and octagon swell with the voice (the Sage's while it speaks, yours while it listens),
 * and it turns red while listening.
 */
class OrbView(context: Context) : View(context) {

    /** "idle", "thinking", "listening" */
    var mode = "idle"
    /** Loudness 0..1 from the voice or the microphone. */
    @Volatile var targetLevel = 0f
    /** True while the Sage's voice is playing. */
    var speakingProbe: () -> Boolean = { false }

    private var level = 0f
    private var angle = 0f
    private var t = 0f
    private var lastNs = 0L

    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val path = Path()

    private companion object {
        val PHI = ((1 + sqrt(5.0)) / 2).toFloat()
        val VERTS = arrayOf(
            floatArrayOf(-1f, PHI, 0f), floatArrayOf(1f, PHI, 0f), floatArrayOf(-1f, -PHI, 0f), floatArrayOf(1f, -PHI, 0f),
            floatArrayOf(0f, -1f, PHI), floatArrayOf(0f, 1f, PHI), floatArrayOf(0f, -1f, -PHI), floatArrayOf(0f, 1f, -PHI),
            floatArrayOf(PHI, 0f, -1f), floatArrayOf(PHI, 0f, 1f), floatArrayOf(-PHI, 0f, -1f), floatArrayOf(-PHI, 0f, 1f),
        )
        val EDGES: List<Pair<Int, Int>> = buildList {
            for (i in 0 until 12) for (j in i + 1 until 12) {
                var d = 0f
                for (k in 0 until 3) d += (VERTS[i][k] - VERTS[j][k]) * (VERTS[i][k] - VERTS[j][k])
                if (kotlin.math.abs(d - 4f) < 0.01f) add(i to j)
            }
        }
    }

    private fun argb(a: Int, rgb: Int) = Color.argb(a.coerceIn(0, 255), Color.red(rgb), Color.green(rgb), Color.blue(rgb))

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val now = System.nanoTime()
        val dt = if (lastNs == 0L) 0.016f else ((now - lastNs) / 1e9f).coerceIn(0f, 0.1f)
        lastNs = now

        // --- motion
        val m = if (mode == "idle" && speakingProbe()) "speaking" else mode
        val k = if (targetLevel > level) 0.55f else 0.18f
        level += (targetLevel - level) * (1f - Math.pow((1 - k).toDouble(), (dt * 30).toDouble()).toFloat())
        val speed = when (m) { "thinking" -> 1.5f; "listening" -> 0.75f; "speaking" -> 0.6f; else -> 0.3f }
        angle += (speed + level * 0.6f) * dt
        t += (if (m == "idle") 2.4f else 7.5f) * dt
        val pulse = (sin(t) + 1f) / 2f
        val base = when (m) { "thinking" -> 0.95f; "listening" -> 1.05f; "speaking" -> 0.95f; else -> 0.85f + 0.04f * pulse }
        val gain = when (m) { "thinking" -> 0f; "listening" -> 0.75f; "speaking" -> 0.7f; else -> 0.35f }
        val scale = base + gain * level
        val red = m == "listening"

        // --- colours
        val lineCol = if (red) Color.rgb(255, 220, 228) else Color.rgb(225, 250, 255)
        val coreCol = if (red) Color.rgb(255, 140, 160) else Color.rgb(150, 245, 235)
        val bloomA = if (red) Color.rgb(140, 28, 48) else Color.rgb(40, 130, 110)
        val bloomB = if (red) Color.rgb(45, 6, 14) else Color.rgb(8, 40, 70)
        val rayCol = if (red) Color.rgb(255, 110, 130) else Color.rgb(170, 240, 255)

        val w = width.toFloat()
        val h = height.toFloat()
        val cx = w / 2
        val cy = h / 2
        val s = min(w, h)

        // soft background bloom
        fill.shader = RadialGradient(cx, cy, s * 0.48f, intArrayOf(argb(150, bloomA), argb(90, bloomB), Color.TRANSPARENT),
            floatArrayOf(0f, 0.55f, 1f), Shader.TileMode.CLAMP)
        canvas.drawCircle(cx, cy, s * 0.48f, fill)
        fill.shader = null

        // faint rays
        line.strokeWidth = s * 0.004f
        for (r in 0 until 18) {
            val a = r * 0.349f + angle * 0.25f + (r % 3) * 0.11f
            line.shader = android.graphics.LinearGradient(cx, cy, cx + cos(a) * s * 0.5f, cy + sin(a) * s * 0.5f,
                argb(90, rayCol), Color.TRANSPARENT, Shader.TileMode.CLAMP)
            canvas.drawLine(cx, cy, cx + cos(a) * s * 0.5f, cy + sin(a) * s * 0.5f, line)
        }
        line.shader = null

        // rotating wireframe
        val ax = 0.45f + angle * 0.6f
        val ay = angle
        val px = FloatArray(12)
        val py = FloatArray(12)
        val pz = FloatArray(12)
        for (i in 0 until 12) {
            var x = VERTS[i][0]
            var y = VERTS[i][1]
            var z = VERTS[i][2]
            val y2 = y * cos(ax) - z * sin(ax)
            val z2 = y * sin(ax) + z * cos(ax)
            y = y2; z = z2
            val x2 = x * cos(ay) + z * sin(ay)
            val z3 = -x * sin(ay) + z * cos(ay)
            x = x2; z = z3
            px[i] = cx + x * s * 0.2f
            py[i] = cy + y * s * 0.2f
            pz[i] = z
        }
        for ((i, j) in EDGES) {
            val depth = (pz[i] + pz[j]) / 2f
            line.strokeWidth = s * 0.016f
            line.color = argb(22, lineCol)
            canvas.drawLine(px[i], py[i], px[j], py[j], line)
            line.strokeWidth = s * 0.0055f
            line.color = argb((120 + 55 * depth).toInt().coerceIn(60, 255), lineCol)
            canvas.drawLine(px[i], py[i], px[j], py[j], line)
        }
        for (i in 0 until 12) {
            fill.color = argb((170 + 40 * pz[i]).toInt(), Color.WHITE)
            canvas.drawCircle(px[i], py[i], s * (0.011f + 0.003f * pz[i]), fill)
        }

        // very thin octagon rings, turning slowly
        for ((ri, radius) in floatArrayOf(0.15f, 0.135f).withIndex()) {
            path.reset()
            for (c in 0 until 8) {
                val a = Math.toRadians(22.5 + 45 * c).toFloat() + angle * 0.15f
                val x = cx + cos(a) * s * radius * scale
                val y = cy + sin(a) * s * radius * scale
                if (c == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            path.close()
            line.strokeWidth = s * 0.018f
            line.color = argb(26, lineCol)
            canvas.drawPath(path, line)
            line.strokeWidth = s * (if (ri == 0) 0.0065f else 0.004f)
            line.color = argb(235, lineCol)
            canvas.drawPath(path, line)
        }

        // small intense core
        val energy = if (m == "idle") 1f else 1.2f + 0.6f * level
        val boost = (0.85f + 0.3f * pulse * energy) * scale
        fill.shader = RadialGradient(cx, cy, s * 0.13f * boost,
            intArrayOf(Color.WHITE, argb(235, coreCol), argb(90, coreCol), Color.TRANSPARENT),
            floatArrayOf(0f, 0.22f, 0.5f, 1f), Shader.TileMode.CLAMP)
        canvas.drawCircle(cx, cy, s * 0.13f * boost, fill)
        fill.shader = null
        val glint = s * (0.13f + 0.04f * pulse) * scale
        line.strokeWidth = s * 0.006f
        line.color = argb(190, Color.WHITE)
        canvas.drawLine(cx - glint, cy, cx + glint, cy, line)
        canvas.drawLine(cx, cy - glint, cx, cy + glint, line)
        fill.color = Color.WHITE
        canvas.drawCircle(cx, cy, s * 0.022f * boost, fill)

        postInvalidateOnAnimation()
    }
}
