package com.greatsage.assistant

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

class Reply(val text: String, val sources: List<String> = emptyList(), val route: String = "")

/**
 * The "mix" brain: uses your PC's free local AI when the phone can reach it (same Wi-Fi),
 * otherwise Claude in the cloud. Phone tools always run on the phone.
 */
class Brain(private val prefs: Prefs, private val tools: ToolRunner) {

    @Volatile private var pcOkUntil = 0L

    companion object {
        fun pcUrl(address: String): String {
            val a = address.trim().removeSuffix("/")
            return if (a.startsWith("http://") || a.startsWith("https://")) a else "http://$a"
        }

        /** Check the PC link. Returns (ok, message). */
        fun ping(address: String, code: String, timeoutMs: Int = 1500): Pair<Boolean, String> = try {
            val (status, body) = Http.request(pcUrl(address) + "/ping", headers = mapOf("X-Sage-Code" to code),
                connectTimeoutMs = timeoutMs, readTimeoutMs = timeoutMs * 2)
            when (status) {
                200 -> true to "Connected to ${JSONObject(body).optString("name", "Great Sage")} on your PC."
                403 -> false to "The PC rejected the pairing code."
                else -> false to "The PC answered with error $status."
            }
        } catch (e: Exception) {
            false to "Could not reach the PC (${e.javaClass.simpleName}). Is it on the same Wi-Fi?"
        }
    }

    class PcUnavailable(msg: String) : IOException(msg)

    fun pcAvailable(): Boolean {
        if (prefs.mode == "claude" || prefs.pcAddress.isBlank()) return false
        if (System.currentTimeMillis() < pcOkUntil) return true
        val ok = ping(prefs.pcAddress, prefs.pcCode).first
        if (ok) pcOkUntil = System.currentTimeMillis() + 30_000
        return ok
    }

    fun chat(text: String): Reply {
        val reply = when (prefs.mode) {
            "pc" -> if (pcAvailable()) pcChat(text)
                    else Reply("Notice. I cannot reach your PC. Make sure it is on, the Great Sage is open, and " +
                        "your phone is on the same Wi-Fi.")
            "claude" -> claudeChat(text)
            else -> {
                if (pcAvailable()) {
                    try {
                        pcChat(text)
                    } catch (e: IOException) {
                        pcOkUntil = 0
                        if (prefs.claudeKey.isNotBlank()) claudeChat(text)
                        else Reply("Notice. The PC stopped responding (${e.message}), and no Claude API key is set.")
                    }
                } else claudeChat(text)
            }
        }
        remember(text, reply.text)
        return reply
    }

    /** Keep the last few turns so follow-ups ("yes", "do that instead") make sense. */
    private fun remember(user: String, assistant: String) {
        val t = prefs.transcript
        t.put(JSONObject().put("role", "user").put("content", user))
        t.put(JSONObject().put("role", "assistant").put("content", assistant))
        val trimmed = JSONArray()
        for (i in maxOf(0, t.length() - 20) until t.length()) trimmed.put(t.get(i))
        prefs.transcript = trimmed
    }

    private fun history(): JSONArray {
        val out = JSONArray()
        val t = prefs.transcript
        for (i in 0 until t.length()) out.put(t.getJSONObject(i))
        return out
    }

    // --- through the PC ---------------------------------------------------------------------------

    private fun pcChat(text: String): Reply {
        val msgs = JSONArray()
        msgs.put(JSONObject().put("role", "system").put("content", Persona.systemPrompt(prefs, "pc")))
        val h = history()
        for (i in 0 until h.length()) msgs.put(h.get(i))
        msgs.put(JSONObject().put("role", "user").put("content", text))
        val used = mutableSetOf<String>()
        var nudged = false
        for (round in 0 until 8) {
            val body = JSONObject().put("messages", msgs).put("tools", PhoneTools.ollamaJson()).toString()
            val (status, resp) = Http.request(Brain.pcUrl(prefs.pcAddress) + "/llm", "POST", body,
                mapOf("X-Sage-Code" to prefs.pcCode), connectTimeoutMs = 3000, readTimeoutMs = 300_000)
            if (status == 403) return Reply("Notice. Your PC rejected the pairing code. Check it in settings.")
            if (status != 200) throw PcUnavailable(
                try { JSONObject(resp).optString("error", "error $status") } catch (e: Exception) { "error $status" })
            val r = JSONObject(resp)
            val newMsgs = r.optJSONArray("new_messages") ?: JSONArray()
            for (i in 0 until newMsgs.length()) msgs.put(newMsgs.get(i))
            val usedArr = r.optJSONArray("used") ?: JSONArray()
            for (i in 0 until usedArr.length()) used.add(usedArr.optString(i))
            val pending = r.optJSONArray("pending_calls") ?: JSONArray()
            if (pending.length() == 0) {
                if (!nudged && "web_search" !in used && Persona.needsVerification(prefs, text)) {
                    nudged = true
                    msgs.put(JSONObject().put("role", "user").put("content", Persona.VERIFY_NUDGE))
                    continue
                }
                return Reply(r.optString("final_text").ifBlank { "Report. Done." }, route = "PC")
            }
            for (i in 0 until pending.length()) {
                val call = pending.getJSONObject(i)
                val name = call.optString("name")
                val args = call.optJSONObject("arguments") ?: JSONObject()
                msgs.put(JSONObject().put("role", "tool").put("content", tools.run(name, args)).put("tool_name", name))
            }
        }
        return Reply("Notice. I could not complete that request.", route = "PC")
    }

    // --- through Claude ---------------------------------------------------------------------------

    private fun claudeChat(text: String): Reply {
        if (prefs.claudeKey.isBlank()) {
            return Reply("Notice. I cannot reach your PC, and no Claude API key is set. Open settings to add one " +
                "or connect to your PC.")
        }
        val msgs = history()
        msgs.put(JSONObject().put("role", "user").put("content", text))
        val toolDefs = PhoneTools.claudeJson()
        toolDefs.put(JSONObject().put("type", "web_search_20250305").put("name", "web_search").put("max_uses", 3))
        var searched = false
        var nudged = false
        val sources = linkedSetOf<String>()
        for (round in 0 until 10) {
            val body = JSONObject()
                .put("model", prefs.claudeModel)
                .put("max_tokens", 1200)
                .put("system", Persona.systemPrompt(prefs, "claude"))
                .put("tools", toolDefs)
                .put("messages", msgs)
                .toString()
            val (status, resp) = Http.request("https://api.anthropic.com/v1/messages", "POST", body, mapOf(
                "x-api-key" to prefs.claudeKey, "anthropic-version" to "2023-06-01"), readTimeoutMs = 120_000)
            if (status != 200) {
                val err = try {
                    JSONObject(resp).getJSONObject("error").optString("message")
                } catch (e: Exception) { "error $status" }
                return Reply("Notice. Claude returned an error: $err", route = "Claude")
            }
            val r = JSONObject(resp)
            val content = r.getJSONArray("content")
            msgs.put(JSONObject().put("role", "assistant").put("content", content))
            val out = StringBuilder()
            val toolUses = mutableListOf<JSONObject>()
            for (i in 0 until content.length()) {
                val b = content.getJSONObject(i)
                when (b.optString("type")) {
                    "text" -> {
                        out.append(b.optString("text"))
                        val cites = b.optJSONArray("citations")
                        if (cites != null) for (j in 0 until cites.length()) {
                            val c = cites.getJSONObject(j)
                            sources.add(c.optString("title").ifBlank { c.optString("url") } + " - " + c.optString("url"))
                        }
                    }
                    "server_tool_use" -> searched = true
                    "tool_use" -> toolUses.add(b)
                }
            }
            if (r.optString("stop_reason") == "pause_turn") continue
            if (toolUses.isEmpty()) {
                val answer = out.toString().trim()
                if (!nudged && !searched && Persona.needsVerification(prefs, text)) {
                    nudged = true
                    msgs.put(JSONObject().put("role", "user").put("content", Persona.VERIFY_NUDGE))
                    continue
                }
                return Reply(answer.ifBlank { "Report. Done." }, sources.toList().take(3), "Claude")
            }
            val results = JSONArray()
            for (u in toolUses) {
                val res = tools.run(u.optString("name"), u.optJSONObject("input") ?: JSONObject())
                results.put(JSONObject().put("type", "tool_result").put("tool_use_id", u.optString("id"))
                    .put("content", res))
            }
            msgs.put(JSONObject().put("role", "user").put("content", results))
        }
        return Reply("Notice. I could not complete that request.", route = "Claude")
    }
}
