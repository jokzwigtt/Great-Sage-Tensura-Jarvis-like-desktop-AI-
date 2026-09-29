package com.greatsage.assistant

import android.app.Activity
import android.app.AlarmManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.Uri
import android.os.BatteryManager
import android.os.Handler
import android.os.Looper
import android.provider.AlarmClock
import android.view.KeyEvent
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/** A tool the AI can use on the phone: name, description, parameters (name, type, description), required. */
class ToolSpec(
    val name: String,
    val description: String,
    val params: List<Triple<String, String, String>>,
    val required: List<String>,
) {
    private fun schema(): JSONObject {
        val props = JSONObject()
        for ((n, type, desc) in params) props.put(n, JSONObject().put("type", type).put("description", desc))
        return JSONObject().put("type", "object").put("properties", props).put("required", JSONArray(required))
    }

    /** Ollama / OpenAI style (used through the PC). */
    fun ollama(): JSONObject = JSONObject().put("type", "function").put(
        "function", JSONObject().put("name", name).put("description", description).put("parameters", schema())
    )

    /** Claude API style. */
    fun claude(): JSONObject =
        JSONObject().put("name", name).put("description", description).put("input_schema", schema())
}

object PhoneTools {
    val specs = listOf(
        ToolSpec("open_app", "Open an app installed on this phone by name, e.g. 'spotify', 'camera', 'discord'.",
            listOf(Triple("name", "string", "App name")), listOf("name")),
        ToolSpec("open_website", "Open a website or a search in the user's Brave browser on the phone " +
            "(e.g. 'youtube', 'reddit.com', 'lofi music').",
            listOf(Triple("query", "string", "A URL or search terms")), listOf("query")),
        ToolSpec("set_timer", "Start a countdown timer on the phone.",
            listOf(Triple("minutes", "number", "Length in minutes"), Triple("label", "string", "Optional name")),
            listOf("minutes")),
        ToolSpec("set_alarm", "Set an alarm on the phone for a clock time like '7:30 am' or '19:00'.",
            listOf(Triple("time", "string", "Clock time"), Triple("text", "string", "Optional label")),
            listOf("time")),
        ToolSpec("set_reminder", "Remind the user about something after some minutes (uses a labelled timer).",
            listOf(Triple("minutes", "number", "Minutes from now"), Triple("text", "string", "What to remind")),
            listOf("minutes", "text")),
        ToolSpec("media_control", "Control music/video on the phone: play, pause, next, previous, stop, " +
            "volume_up, volume_down, set_volume (amount = percent), mute.",
            listOf(Triple("action", "string", "The action"), Triple("amount", "number", "Percent, optional")),
            listOf("action")),
        ToolSpec("call_number", "Open the phone dialer with a number ready to call (the user presses call).",
            listOf(Triple("number", "string", "Phone number")), listOf("number")),
        ToolSpec("send_text", "Open a text message to a phone number with the message filled in (the user " +
            "presses send).",
            listOf(Triple("number", "string", "Phone number"), Triple("message", "string", "Message text")),
            listOf("number", "message")),
        ToolSpec("status_report", "Phone status report: time, battery, local weather, next alarm.", listOf(), listOf()),
        ToolSpec("remember", "Save a fact about the user to long-term memory.",
            listOf(Triple("fact", "string", "The fact")), listOf("fact")),
        ToolSpec("forget", "Remove remembered facts containing the given text.",
            listOf(Triple("text", "string", "Text to match")), listOf("text")),
        ToolSpec("set_user_title", "Change what you call the user (default 'Master'). Use 'none' for no title.",
            listOf(Triple("title", "string", "New title")), listOf("title")),
    )

    fun ollamaJson(): JSONArray = JSONArray().also { a -> specs.forEach { a.put(it.ollama()) } }
    fun claudeJson(): JSONArray = JSONArray().also { a -> specs.forEach { a.put(it.claude()) } }
}

/** Carries out the phone tools. Called from background threads. */
class ToolRunner(private val activity: Activity, private val prefs: Prefs) {
    private val main = Handler(Looper.getMainLooper())

    /** Run something on the UI thread and wait for it. */
    private fun <T> ui(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        var result: Result<T>? = null
        val latch = CountDownLatch(1)
        main.post {
            result = runCatching(block)
            latch.countDown()
        }
        latch.await(10, TimeUnit.SECONDS)
        return result?.getOrThrow() ?: throw IllegalStateException("The screen did not respond.")
    }

    private fun start(intent: Intent) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        ui { activity.startActivity(intent) }
    }

    fun run(name: String, args: JSONObject): String = try {
        when (name) {
            "open_app" -> openApp(args.optString("name"))
            "open_website" -> openWebsite(args.optString("query"))
            "set_timer" -> setTimer(args.optDouble("minutes", 1.0), args.optString("label", ""))
            "set_reminder" -> setTimer(args.optDouble("minutes", 1.0), "Reminder: " + args.optString("text"))
            "set_alarm" -> setAlarm(args.optString("time"), args.optString("text", ""))
            "media_control" -> media(args.optString("action"),
                if (args.has("amount") && !args.isNull("amount")) args.optDouble("amount").roundToInt() else null)
            "call_number" -> {
                start(Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(args.optString("number")))))
                "The dialer is open with the number ready."
            }
            "send_text" -> {
                val i = Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(args.optString("number"))))
                i.putExtra("sms_body", args.optString("message"))
                start(i)
                "The message is ready to send."
            }
            "status_report" -> statusReport()
            "remember" -> {
                prefs.memory = prefs.memory + args.optString("fact")
                "Remembered: ${args.optString("fact")}"
            }
            "forget" -> {
                val t = args.optString("text").lowercase()
                val before = prefs.memory.size
                prefs.memory = prefs.memory.filterNot { it.lowercase().contains(t) }
                "Forgot ${before - prefs.memory.size} item(s)."
            }
            "set_user_title" -> {
                val t = args.optString("title").trim()
                prefs.userTitle = if (t.lowercase() in listOf("none", "nothing", "no title")) "" else t
                if (prefs.userTitle.isEmpty()) "The user will not be given a title."
                else "From now on the user is addressed as '${prefs.userTitle}'."
            }
            else -> "Unknown tool '$name'."
        }
    } catch (e: Exception) {
        "Error: ${e.message}"
    }

    // --- apps & web -------------------------------------------------------------------------------

    private fun isInstalled(pkg: String): Boolean = try {
        activity.packageManager.getPackageInfo(pkg, 0)
        true
    } catch (e: PackageManager.NameNotFoundException) {
        false
    }

    private fun openApp(name: String): String {
        val pm = activity.packageManager
        val n = name.lowercase().trim()
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = pm.queryIntentActivities(launcher, 0).map { it to it.loadLabel(pm).toString() }
        val match = apps.firstOrNull { it.second.lowercase() == n }
            ?: apps.filter { it.second.lowercase().startsWith(n) }.minByOrNull { it.second.length }
            ?: apps.filter { it.second.lowercase().contains(n) }.minByOrNull { it.second.length }
        if (match == null) {
            if (Persona.KNOWN_SITES.containsKey(n)) return openWebsite(n)
            return "Couldn't find an app called '$name' on this phone."
        }
        val info = match.first.activityInfo
        val intent = pm.getLaunchIntentForPackage(info.packageName)
            ?: Intent(Intent.ACTION_MAIN).setClassName(info.packageName, info.name)
        start(intent)
        return "Opened ${match.second}."
    }

    private fun openWebsite(query: String): String {
        val url = Persona.toUrl(query)
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        return if (isInstalled("com.brave.browser")) {
            intent.setPackage("com.brave.browser")
            start(intent)
            "Opened $url in Brave."
        } else {
            start(intent)
            "Brave isn't installed, so opened $url in the default browser."
        }
    }

    // --- timers & alarms --------------------------------------------------------------------------

    private fun setTimer(minutes: Double, label: String): String {
        val seconds = (minutes * 60).roundToInt().coerceIn(1, 86399)
        val i = Intent(AlarmClock.ACTION_SET_TIMER)
            .putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        if (label.isNotBlank()) i.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        start(i)
        return "Timer started for ${if (seconds >= 60) "${seconds / 60} minute(s)" else "$seconds seconds"}."
    }

    private fun setAlarm(time: String, text: String): String {
        val m = Regex("^\\s*(\\d{1,2})(?::(\\d{2}))?\\s*([ap])?\\.?\\s*m?\\.?\\s*$").matchEntire(time.lowercase())
            ?: return "Could not understand the time '$time'."
        var h = m.groupValues[1].toInt()
        val min = m.groupValues[2].ifEmpty { "0" }.toInt()
        val ap = m.groupValues[3]
        if (ap == "p" && h < 12) h += 12
        if (ap == "a" && h == 12) h = 0
        val i = Intent(AlarmClock.ACTION_SET_ALARM)
            .putExtra(AlarmClock.EXTRA_HOUR, h % 24)
            .putExtra(AlarmClock.EXTRA_MINUTES, min)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        if (text.isNotBlank()) i.putExtra(AlarmClock.EXTRA_MESSAGE, text)
        start(i)
        return "Alarm set for %d:%02d %s.".format(if (h % 12 == 0) 12 else h % 12, min, if (h < 12) "AM" else "PM")
    }

    // --- media ------------------------------------------------------------------------------------

    fun media(action: String, amount: Int?): String {
        val am = activity.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        fun key(code: Int) {
            am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
            am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
        }
        val stream = AudioManager.STREAM_MUSIC
        val max = am.getStreamMaxVolume(stream)
        return when (action.lowercase().replace(" ", "_")) {
            "play" -> { key(KeyEvent.KEYCODE_MEDIA_PLAY); "Playback resumed." }
            "pause" -> { key(KeyEvent.KEYCODE_MEDIA_PAUSE); "Playback paused." }
            "play_pause" -> { key(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE); "Playback toggled." }
            "next" -> { key(KeyEvent.KEYCODE_MEDIA_NEXT); "Skipped to the next track." }
            "previous" -> { key(KeyEvent.KEYCODE_MEDIA_PREVIOUS); "Returned to the previous track." }
            "stop" -> { key(KeyEvent.KEYCODE_MEDIA_STOP); "Playback stopped." }
            "mute" -> {
                am.adjustStreamVolume(stream, AudioManager.ADJUST_TOGGLE_MUTE, AudioManager.FLAG_SHOW_UI)
                "Sound muted or unmuted."
            }
            "volume_up", "volume_down" -> {
                val steps = ((amount ?: 10) / 100.0 * max).roundToInt().coerceAtLeast(1)
                val dir = if (action.contains("up")) AudioManager.ADJUST_RAISE else AudioManager.ADJUST_LOWER
                repeat(steps) { am.adjustStreamVolume(stream, dir, if (it == steps - 1) AudioManager.FLAG_SHOW_UI else 0) }
                "Volume ${if (action.contains("up")) "raised" else "lowered"} by about ${amount ?: 10} percent."
            }
            "set_volume" -> {
                val level = (amount ?: 50).coerceIn(0, 100)
                am.setStreamVolume(stream, (level / 100.0 * max).roundToInt(), AudioManager.FLAG_SHOW_UI)
                "Volume set to $level percent."
            }
            else -> "Unknown media action '$action'."
        }
    }

    // --- status report ----------------------------------------------------------------------------

    fun statusReport(): String {
        val now = Date()
        val parts = mutableListOf("Report. It is " + SimpleDateFormat("h:mm a, EEEE, MMMM d", Locale.US).format(now) + ".")
        val bm = activity.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        parts.add("Battery at $pct percent${if (bm.isCharging) ", charging" else ""}.")
        parts.add(try { weather() } catch (e: Exception) { "Weather data is unavailable right now." })
        val alarm = (activity.getSystemService(Context.ALARM_SERVICE) as AlarmManager).nextAlarmClock
        if (alarm != null) {
            val c = Calendar.getInstance().apply { timeInMillis = alarm.triggerTime }
            parts.add("Next alarm: " + SimpleDateFormat("h:mm a EEEE", Locale.US).format(c.time) + ".")
        }
        return parts.joinToString(" ")
    }

    private fun weather(): String {
        val (_, geoText) = Http.request("https://ipapi.co/json/", readTimeoutMs = 8000)
        val geo = JSONObject(geoText)
        val lat = geo.getDouble("latitude")
        val lon = geo.getDouble("longitude")
        val place = geo.optString("city", "your area")
        val url = "https://api.open-meteo.com/v1/forecast?latitude=$lat&longitude=$lon&timezone=auto" +
            "&forecast_days=1&temperature_unit=fahrenheit&current=temperature_2m,weather_code" +
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        val w = JSONObject(Http.request(url, readTimeoutMs = 8000).second)
        val cur = w.getJSONObject("current")
        val day = w.getJSONObject("daily")
        val codes = mapOf(0 to "clear", 1 to "mostly clear", 2 to "partly cloudy", 3 to "overcast", 45 to "foggy",
            48 to "foggy", 51 to "light drizzle", 53 to "drizzle", 55 to "heavy drizzle", 61 to "light rain",
            63 to "rain", 65 to "heavy rain", 71 to "light snow", 73 to "snow", 75 to "heavy snow",
            80 to "rain showers", 81 to "rain showers", 82 to "heavy rain showers", 95 to "thunderstorms")
        return "In $place it is ${cur.getDouble("temperature_2m").roundToInt()} degrees and " +
            "${codes[cur.getInt("weather_code")] ?: "unsettled"}, with a high of " +
            "${day.getJSONArray("temperature_2m_max").getDouble(0).roundToInt()} and a low of " +
            "${day.getJSONArray("temperature_2m_min").getDouble(0).roundToInt()}. Chance of rain: " +
            "${day.getJSONArray("precipitation_probability_max").optInt(0)} percent."
    }
}
