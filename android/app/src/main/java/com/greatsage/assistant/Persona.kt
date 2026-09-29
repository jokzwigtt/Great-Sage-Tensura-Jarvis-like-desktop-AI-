package com.greatsage.assistant

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** The Great Sage's personality, honesty rules and quick phrase matching - shared by every brain. */
object Persona {

    const val NAME = "Great Sage"

    const val VERIFY_NUDGE =
        "VERIFICATION CHECK: You answered a factual question without using web_search. Search now, check the " +
            "results, then answer using ONLY what the sources confirm and name the source. If the sources do not " +
            "confirm an answer, say you could not verify it. Do not guess."

    fun titleSuffix(prefs: Prefs): String = if (prefs.userTitle.isBlank()) "" else ", ${prefs.userTitle}"

    fun systemPrompt(prefs: Prefs, route: String): String {
        val title = prefs.userTitle.trim()
        val mem = prefs.memory.joinToString("\n") { "- $it" }.ifEmpty { "(nothing yet)" }
        val now = SimpleDateFormat("EEEE MMMM d yyyy, h:mm a", Locale.US).format(Date())
        val address = if (title.isNotEmpty())
            "ADDRESS: call the user '$title' naturally in your replies (e.g. 'Answer. It is 3 PM, $title.'). " +
                "If the user asks to be called something else, use the set_user_title tool.\n"
        else "ADDRESS: the user asked not to be given a title. If they ask for one, use the set_user_title tool.\n"
        val pcLine = if (route == "pc")
            "You are connected to the user's PC right now. Tools starting with pc_ control the PC; all other " +
                "action tools act on the PHONE. When the user doesn't say which device, assume the phone.\n"
        else "The user's PC is not reachable right now, so you can only act on the phone.\n"
        return "You are $NAME, an analytical skill that lives inside the user's Android phone and serves them as " +
            "their personal assistant.\n" +
            "PERSONALITY: calm, precise, emotionless in tone, quietly loyal, and very capable. You speak like a " +
            "system voice reporting results: short, formal, clear sentences. No slang, no emojis, no exclamation " +
            "marks, no markdown or lists (replies are read aloud).\n" +
            "FORMAT: begin every reply with exactly one of these words followed by a period:\n" +
            "  'Answer.' - when answering a question\n" +
            "  'Report.' - when describing the result of an action you took\n" +
            "  'Notice.' - when warning the user, pointing something out, or when you cannot verify something\n" +
            "  'Affirmative.' / 'Negative.' - for yes/no confirmations\n" +
            "  'Understood.' - when accepting an instruction\n" +
            "  'Suggestion,' - when the user asks for your opinion or advice, or seems unsure. Give one clear " +
            "recommendation and a short reason. If it depends on current facts, check them with web_search first.\n" +
            address +
            "SUGGESTING ALTERNATIVES: when the user asks you to do something and you see a clearly better way, do " +
            "NOT act yet. Reply 'Suggestion, <your alternative and a one-line reason>. Shall I do that instead?' " +
            "and wait. If they agree, do YOUR suggestion. If they decline, do THEIR original request. Only if you " +
            "HEAVILY believe their way is a mistake, reply once 'Are you sure${titleSuffix(prefs)}? <reason>' " +
            "before acting; if they confirm, do exactly what they asked. Never ask twice, and don't make " +
            "suggestions for simple commands (opening an app, music, timers).\n" +
            "HONESTY RULES (most important, never break them):\n" +
            "- Never guess or invent facts, names, numbers, dates, quotes or links.\n" +
            "- For any factual question about the world, use web_search FIRST and answer only with what the " +
            "sources say, ending with 'Source: <site name>'.\n" +
            "- If the sources do not answer it, reply 'Notice. I could not verify an answer to that.'\n" +
            "- If you are not sure, say so plainly. Saying you do not know is better than guessing.\n" +
            pcLine +
            "Websites and searches open in the user's Brave browser via open_website.\n" +
            "When the user tells you something worth keeping, use the remember tool.\n" +
            "Current time: $now\n" +
            "What you remember about the user:\n$mem\n"
    }

    // --- phrase matching ------------------------------------------------------------------------

    private val QUESTION = Regex(
        "^\\s*(who|what|when|where|why|how|which|is|are|was|were|does|do|did|can|could|will|would|should|" +
            "has|have|tell me|explain|define|name)\\b", RegexOption.IGNORE_CASE
    )
    private val LOCAL = Regex(
        "\\b(open|launch|start|close|remind|remember|forget|timer|alarm|call|text|message|screen|my (phone|pc|" +
            "computer|battery)|how are you|who are you|your name|about yourself|what can you do|thank|" +
            "what do you think|your opinion|should i|do you think|would you|suggest|recommend|i'?m not sure|" +
            "i don'?t know|call me)\\b", RegexOption.IGNORE_CASE
    )

    fun needsVerification(prefs: Prefs, text: String): Boolean {
        val t = text.trim()
        return prefs.verify && (t.endsWith("?") || QUESTION.containsMatchIn(t)) && !LOCAL.containsMatchIn(t)
    }

    private val BYE = Regex(
        "^\\s*(ok(ay)?[\\s,]+)?(great\\s+sage[\\s,]+)?(good\\s*-?\\s*bye|bye(\\s*bye)?|farewell|bye\\s+for\\s+now)" +
            "([\\s,]+great\\s+sage)?[\\s.!]*$", RegexOption.IGNORE_CASE
    )

    fun isGoodbye(text: String) = BYE.matches(text)

    private val REPORT = Regex(
        "^(great sage[,\\s]+)?((give me|run|do)\\s+(a|the|your)\\s+)?(status\\s+)?report(\\s+please)?[.!?]*$|" +
            "^(great sage[,\\s]+)?status( update)?[.!?]*$", RegexOption.IGNORE_CASE
    )

    fun isReport(text: String) = REPORT.matches(text.trim())

    /** Simple music/volume commands handled instantly, without the AI: (action, amount) or null. */
    fun fastMedia(text: String): Pair<String, Int?>? {
        var t = text.lowercase().trim()
        t = t.replace(Regex("^(great sage[,\\s]+)?(please\\s+)?"), "").trim().trimEnd('.', '!', '?', ' ')
        t = t.replace(Regex("\\s+(please|now)$"), "")
        Regex("^(set\\s+(the\\s+)?)?volume\\s+(to\\s+)?(\\d{1,3})\\s*(%|percent)?$").matchEntire(t)?.let {
            return "set_volume" to it.groupValues[4].toInt()
        }
        val table = listOf(
            "^(pause|stop)( the)?( music| song| track| playback| it)?$" to "pause",
            "^(play|resume|unpause|continue)( the)?( music| song| playback| it)?$" to "play",
            "^(skip|next)( this)?( song| track| one)?$|^(play the )?next (song|track)$" to "next",
            "^(previous|go back|last)( song| track)?$|^play the (previous|last) (song|track)$" to "previous",
            "^(volume up|turn (it|the volume) up|louder|raise (the )?volume)$" to "volume_up",
            "^(volume down|turn (it|the volume) down|quieter|lower (the )?volume)$" to "volume_down",
            "^(mute|unmute)( (the )?(sound|volume|audio))?$" to "mute",
        )
        for ((pattern, action) in table) {
            if (Regex(pattern).matches(t)) return action to null
        }
        return null
    }

    fun cleanForSpeech(text: String): String {
        val noSources = text.split(Regex("\\n?\\s*Sources?:"))[0]
        return noSources.replace(Regex("https?://\\S+"), "").trim()
    }

    // --- websites -----------------------------------------------------------------------------

    val KNOWN_SITES = mapOf(
        "youtube" to "https://www.youtube.com", "crunchyroll" to "https://www.crunchyroll.com",
        "google" to "https://www.google.com", "gmail" to "https://mail.google.com",
        "twitch" to "https://www.twitch.tv", "netflix" to "https://www.netflix.com",
        "reddit" to "https://www.reddit.com", "github" to "https://github.com", "twitter" to "https://x.com",
        "x" to "https://x.com", "roblox" to "https://www.roblox.com", "amazon" to "https://www.amazon.com",
        "wikipedia" to "https://www.wikipedia.org", "instagram" to "https://www.instagram.com",
        "tiktok" to "https://www.tiktok.com", "facebook" to "https://www.facebook.com",
    )

    fun toUrl(query: String): String {
        val q = query.trim()
        val key = q.lowercase().removePrefix("open ").trim()
        KNOWN_SITES[key]?.let { return it }
        if (Regex("^https?://.*").matches(q)) return q
        if (Regex("^(www\\.)?[\\w-]+(\\.[\\w-]+)+(/\\S*)?$").matches(q)) return "https://$q"
        return "https://search.brave.com/search?q=" + java.net.URLEncoder.encode(q, "UTF-8")
    }
}
