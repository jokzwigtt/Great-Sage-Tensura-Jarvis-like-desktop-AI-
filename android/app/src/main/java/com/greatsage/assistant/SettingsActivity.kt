package com.greatsage.assistant

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

class SettingsActivity : Activity() {
    private lateinit var prefs: Prefs

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun box(color: Int, stroke: Int) = GradientDrawable().apply {
        setColor(color)
        setCornerRadius(dp(6).toFloat())
        setStroke(dp(1), stroke)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(20), dp(18), dp(28))
            setBackgroundColor(BG)
        }

        fun heading(t: String) = col.addView(TextView(this).apply {
            text = t
            setTextColor(CYAN)
            textSize = 16f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            setPadding(0, dp(18), 0, dp(4))
        })

        fun note(t: String) = col.addView(TextView(this).apply {
            text = t
            setTextColor(DIM)
            textSize = 12f
            setPadding(0, 0, 0, dp(6))
        })

        fun field(hint: String, value: String, password: Boolean = false): EditText {
            val e = EditText(this).apply {
                this.hint = hint
                setText(value)
                setTextColor(CYAN_SOFT)
                setHintTextColor(DIM)
                typeface = Typeface.MONOSPACE
                background = box(PANEL, Color.rgb(27, 118, 168))
                setPadding(dp(12), dp(10), dp(12), dp(10))
                setSingleLine(true)
                if (password) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            }
            col.addView(e, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(8) })
            return e
        }

        fun action(label: String, onClick: () -> Unit) = col.addView(TextView(this).apply {
            text = label
            gravity = Gravity.CENTER
            setTextColor(CYAN_SOFT)
            textSize = 15f
            typeface = Typeface.MONOSPACE
            background = box(Color.rgb(15, 58, 85), CYAN)
            setPadding(dp(12), dp(12), dp(12), dp(12))
            setOnClickListener { onClick() }
        }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) })

        col.addView(TextView(this).apply {
            text = "G R E A T   S A G E   ·   S E T T I N G S"
            setTextColor(CYAN)
            textSize = 16f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        })

        heading("Your PC (free, private brain)")
        note("On your PC, say or type \"phone link\" to the Great Sage. It shows the address and pairing code. " +
            "Your phone must be on the same Wi-Fi.")
        val pc = field("PC address, e.g. 192.168.1.23:47632", prefs.pcAddress)
        val code = field("Pairing code", prefs.pcCode)
        action("Test PC connection") {
            Thread {
                val (_, msg) = Brain.ping(pc.text.toString(), code.text.toString(), 3000)
                runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }
            }.start()
        }

        heading("Claude (used when your PC isn't reachable)")
        note("Get an API key at console.anthropic.com. Costs a little per message.")
        val key = field("Claude API key", prefs.claudeKey, password = true)
        val model = field("Claude model", prefs.claudeModel)

        heading("Which brain")
        val group = RadioGroup(this)
        val modes = listOf("auto" to "Auto: PC when reachable, otherwise Claude", "pc" to "PC only", "claude" to "Claude only")
        val buttons = modes.map { (id, label) ->
            RadioButton(this).apply {
                text = label
                setTextColor(CYAN_SOFT)
                this.id = android.view.View.generateViewId()
                tag = id
                group.addView(this)
            }
        }
        buttons.firstOrNull { it.tag == prefs.mode }?.isChecked = true
        col.addView(group)

        heading("You")
        val title = field("What the Sage calls you (empty = nothing)", prefs.userTitle)
        val speak = CheckBox(this).apply {
            text = "Speak replies out loud"
            setTextColor(CYAN_SOFT)
            isChecked = prefs.speak
        }
        col.addView(speak)
        val verify = CheckBox(this).apply {
            text = "Verify facts with a web search"
            setTextColor(CYAN_SOFT)
            isChecked = prefs.verify
        }
        col.addView(verify)

        action("Save") {
            prefs.pcAddress = pc.text.toString()
            prefs.pcCode = code.text.toString()
            prefs.claudeKey = key.text.toString()
            prefs.claudeModel = model.text.toString()
            prefs.userTitle = title.text.toString()
            prefs.mode = buttons.firstOrNull { it.isChecked }?.tag as? String ?: "auto"
            prefs.speak = speak.isChecked
            prefs.verify = verify.isChecked
            Toast.makeText(this, "Saved.", Toast.LENGTH_SHORT).show()
            finish()
        }

        heading("Replace Google's assistant")
        note("Choose Great Sage as the \"Digital assistant app\". Then holding the power button (or swiping up " +
            "from a bottom corner) opens the Great Sage. On Samsung: Settings > Advanced features > Side button > " +
            "Press and hold > Digital assistant.")
        action("Open assistant settings") {
            val tries = listOf(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS),
                Intent(Settings.ACTION_VOICE_INPUT_SETTINGS), Intent(Settings.ACTION_SETTINGS))
            for (i in tries) {
                try {
                    startActivity(i)
                    break
                } catch (e: Exception) {
                }
            }
        }
        action("Clear conversation history") {
            prefs.transcript = org.json.JSONArray()
            Toast.makeText(this, "Conversation history cleared.", Toast.LENGTH_SHORT).show()
        }

        setContentView(ScrollView(this).apply {
            setBackgroundColor(BG)
            addView(col)
        })
    }
}
