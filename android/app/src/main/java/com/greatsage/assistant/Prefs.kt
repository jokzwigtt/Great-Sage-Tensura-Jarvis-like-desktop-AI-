package com.greatsage.assistant

import android.content.Context
import org.json.JSONArray

/** Everything the app remembers, stored privately on the phone. */
class Prefs(context: Context) {
    private val p = context.getSharedPreferences("great_sage", Context.MODE_PRIVATE)

    private fun str(key: String, def: String): String = p.getString(key, def) ?: def
    private fun put(key: String, value: String) {
        p.edit().putString(key, value).apply()
    }

    /** PC address shown by the Great Sage on your PC, e.g. 192.168.1.23:47632 */
    var pcAddress: String
        get() = str("pc_address", "")
        set(v) = put("pc_address", v.trim())

    /** 6-digit pairing code shown by the Great Sage on your PC */
    var pcCode: String
        get() = str("pc_code", "")
        set(v) = put("pc_code", v.trim())

    var claudeKey: String
        get() = str("claude_key", "")
        set(v) = put("claude_key", v.trim())

    var claudeModel: String
        get() = str("claude_model", "claude-sonnet-5-5")
        set(v) = put("claude_model", v.trim().ifEmpty { "claude-sonnet-5-5" })

    /** What the Sage calls you. Empty = no title. */
    var userTitle: String
        get() = str("user_title", "Master")
        set(v) = put("user_title", v.trim())

    /** "auto" = PC when reachable, otherwise Claude; "pc" = PC only; "claude" = Claude only */
    var mode: String
        get() = str("mode", "auto")
        set(v) = put("mode", v)

    var speak: Boolean
        get() = p.getBoolean("speak", true)
        set(v) {
            p.edit().putBoolean("speak", v).apply()
        }

    var verify: Boolean
        get() = p.getBoolean("verify", true)
        set(v) {
            p.edit().putBoolean("verify", v).apply()
        }

    var memory: List<String>
        get() {
            val a = try {
                JSONArray(str("memory", "[]"))
            } catch (e: Exception) {
                JSONArray()
            }
            return List(a.length()) { a.optString(it) }
        }
        set(v) = put("memory", JSONArray(v).toString())

    /** Recent conversation (plain text turns), so the Sage remembers context between uses. */
    var transcript: JSONArray
        get() = try {
            JSONArray(str("transcript", "[]"))
        } catch (e: Exception) {
            JSONArray()
        }
        set(v) = put("transcript", v.toString())
}
