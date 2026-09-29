package com.greatsage.assistant

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject

val BG = Color.rgb(5, 10, 20)
val PANEL = Color.rgb(10, 22, 40)
val CYAN = Color.rgb(41, 211, 255)
val CYAN_SOFT = Color.rgb(191, 239, 255)
val DIM = Color.rgb(91, 122, 153)

class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var tools: ToolRunner
    private lateinit var brain: Brain
    private lateinit var voice: Voice
    private lateinit var ears: Ears
    private lateinit var orb: OrbView
    private lateinit var log: TextView
    private lateinit var scroll: ScrollView
    private lateinit var input: EditText
    private lateinit var status: TextView
    private lateinit var mic: TextView
    private val main = Handler(Looper.getMainLooper())
    private val logText = SpannableStringBuilder()
    private var busy = false

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        tools = ToolRunner(this, prefs)
        brain = Brain(prefs, tools)
        buildUi()
        voice = Voice(this) { orb.targetLevel = it }
        orb.speakingProbe = { voice.speaking }
        ears = Ears(this,
            onLevel = { orb.targetLevel = it },
            onPartial = { main.post { status.text = it } },
            onResult = { text -> main.post { heard(text) } })
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
        }
        handleLaunch(intent, first = true)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleLaunch(intent, first = false)
    }

    private fun handleLaunch(i: Intent?, first: Boolean) {
        val assist = i?.getBooleanExtra("assist", false) == true || i?.action == Intent.ACTION_ASSIST
        if (assist) {
            main.postDelayed({ startListening() }, 300)          // opened by the assistant button: listen right away
        } else if (first) {
            val greeting = "Notice. Great Sage is online. Awaiting your query${Persona.titleSuffix(prefs)}."
            addMessage(Persona.NAME, greeting)
            if (prefs.pcAddress.isBlank() && prefs.claudeKey.isBlank()) {
                addMessage(Persona.NAME, "Notice. Tap ⚙ to connect your PC or add a Claude API key before we begin.")
            }
            if (prefs.speak) voice.speak(greeting)
        }
    }

    // --- screen -----------------------------------------------------------------------------------

    private fun box(color: Int, stroke: Int = 0) = GradientDrawable().apply {
        setColor(color)
        setCornerRadius(dp(6).toFloat())
        if (stroke != 0) setStroke(dp(1), stroke)
    }

    private fun buildUi() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(BG)
            setPadding(dp(14), dp(18), dp(14), dp(10))
        }

        val header = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        orb = OrbView(this)
        orb.setOnClickListener { if (ears.listening) ears.stop() else startListening() }
        header.addView(orb, LinearLayout.LayoutParams(dp(104), dp(104)))
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(10), 0, 0, 0) }
        titles.addView(TextView(this).apply {
            text = "G R E A T   S A G E"
            setTextColor(CYAN)
            textSize = 19f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        })
        titles.addView(TextView(this).apply {
            text = "analysis · appraisal · assistance"
            setTextColor(DIM)
            textSize = 11f
            typeface = Typeface.MONOSPACE
        })
        header.addView(titles, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        header.addView(TextView(this).apply {
            text = "⚙"
            textSize = 26f
            setTextColor(CYAN_SOFT)
            setPadding(dp(12), dp(8), dp(4), dp(8))
            setOnClickListener { startActivity(Intent(this@MainActivity, SettingsActivity::class.java)) }
        })
        root.addView(header)

        log = TextView(this).apply {
            setTextColor(CYAN_SOFT)
            textSize = 15f
            typeface = Typeface.MONOSPACE
            setPadding(dp(12), dp(12), dp(12), dp(12))
            setTextIsSelectable(true)
        }
        scroll = ScrollView(this).apply {
            background = box(PANEL, CYAN)
            addView(log)
        }
        root.addView(scroll, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f).apply {
            topMargin = dp(12); bottomMargin = dp(10)
        })

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        input = EditText(this).apply {
            hint = "Speak your query..."
            setHintTextColor(DIM)
            setTextColor(CYAN_SOFT)
            typeface = Typeface.MONOSPACE
            background = box(PANEL, Color.rgb(27, 118, 168))
            setPadding(dp(12), dp(10), dp(12), dp(10))
            setSingleLine(true)
            imeOptions = EditorInfo.IME_ACTION_SEND
            setOnEditorActionListener { _, id, _ ->
                if (id == EditorInfo.IME_ACTION_SEND) { send(); true } else false
            }
        }
        row.addView(input, LinearLayout.LayoutParams(0, dp(48), 1f))
        mic = button("◉") { if (ears.listening) ears.stop() else startListening() }
        row.addView(mic, LinearLayout.LayoutParams(dp(52), dp(48)).apply { leftMargin = dp(8) })
        row.addView(button("Send") { send() }, LinearLayout.LayoutParams(dp(72), dp(48)).apply { leftMargin = dp(8) })
        root.addView(row)

        status = TextView(this).apply {
            setTextColor(DIM)
            textSize = 12f
            gravity = Gravity.CENTER
            typeface = Typeface.MONOSPACE
            setPadding(0, dp(8), 0, 0)
            text = "Standing by"
        }
        root.addView(status)
        setContentView(root)
    }

    private fun button(label: String, onClick: () -> Unit) = TextView(this).apply {
        text = label
        gravity = Gravity.CENTER
        setTextColor(CYAN_SOFT)
        textSize = 16f
        typeface = Typeface.MONOSPACE
        background = box(Color.rgb(15, 58, 85), CYAN)
        setOnClickListener { onClick() }
    }

    private fun addMessage(who: String, text: String) {
        val user = who == "You"
        var start = logText.length
        logText.append("《$who》\n")
        logText.setSpan(ForegroundColorSpan(if (user) DIM else CYAN), start, logText.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        start = logText.length
        logText.append(text).append("\n\n")
        logText.setSpan(ForegroundColorSpan(if (user) Color.rgb(230, 237, 245) else CYAN_SOFT), start, logText.length,
            Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        log.text = logText
        scroll.post { scroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun setState(mode: String, text: String) {
        orb.mode = mode
        status.text = text
    }

    // --- talking ----------------------------------------------------------------------------------

    private fun startListening() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
            return
        }
        if (busy) return
        voice.stop()
        setState("listening", "Listening...")
        ears.start()
    }

    private fun heard(text: String?) {
        if (text.isNullOrBlank()) {
            setState("idle", "Notice. No speech detected.")
            return
        }
        send(text)
    }

    private fun send(given: String? = null) {
        val text = (given ?: input.text.toString()).trim()
        if (text.isEmpty() || busy) return
        input.setText("")
        addMessage("You", text)

        if (Persona.isGoodbye(text)) {
            val bye = "Understood. Until next time${Persona.titleSuffix(prefs)}."
            addMessage(Persona.NAME, bye)
            if (prefs.speak) voice.speak(bye) { finish() } else finish()
            return
        }
        val quick: (() -> String)? = when {
            Persona.isReport(text) -> { { tools.statusReport() } }
            Persona.fastMedia(text) != null -> {
                val (action, amount) = Persona.fastMedia(text)!!
                ({ "Report. " + tools.media(action, amount) })
            }
            else -> null
        }
        busy = true
        setState("thinking", "Analyzing...")
        Thread {
            val reply = try {
                if (quick != null) Reply(quick()) else brain.chat(text)
            } catch (e: Exception) {
                Reply("Notice. An error occurred: ${e.message}")
            }
            main.post { finishReply(reply) }
        }.start()
    }

    private fun finishReply(reply: Reply) {
        busy = false
        val shown = if (reply.sources.isEmpty()) reply.text
            else reply.text + "\nSources: " + reply.sources.joinToString("; ")
        addMessage(Persona.NAME, shown)
        setState("idle", if (reply.route.isEmpty()) "Standing by" else "Standing by  ·  via ${reply.route}")
        val asked = reply.text.trim().endsWith("?")
        if (prefs.speak) {
            voice.speak(reply.text) { if (asked) startListening() }     // a question: listen for the answer
        } else if (asked) {
            startListening()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        voice.shutdown()
        ears.destroy()
    }
}
