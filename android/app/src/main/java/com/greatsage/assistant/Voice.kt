package com.greatsage.assistant

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognitionService
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale
import kotlin.math.log10
import kotlin.math.sqrt

/** The Sage's voice. Speech is rendered to a file and played here, so the orb can follow every syllable. */
class Voice(private val context: Context, private val onLevel: (Float) -> Unit) : TextToSpeech.OnInitListener {
    private val main = Handler(Looper.getMainLooper())
    private val tts = TextToSpeech(context, this)
    private var ready = false
    private var queued: Pair<String, (() -> Unit)?>? = null
    @Volatile var speaking = false
        private set
    @Volatile private var stopFlag = false

    override fun onInit(status: Int) {
        if (status != TextToSpeech.SUCCESS) return
        tts.setLanguage(Locale.US)
        tts.setSpeechRate(0.95f)
        try {   // prefer an offline female English voice when the engine labels one
            val voices = tts.voices ?: emptySet()
            val pick = voices.filter { it.locale.language == "en" && !it.isNetworkConnectionRequired }
                .firstOrNull { it.name.lowercase().contains("female") }
            if (pick != null) tts.setVoice(pick)
        } catch (e: Exception) { }
        ready = true
        queued?.let { speak(it.first, it.second) }
        queued = null
    }

    fun speak(text: String, done: (() -> Unit)? = null) {
        val clean = Persona.cleanForSpeech(text)
        if (clean.isBlank()) {
            done?.invoke()
            return
        }
        if (!ready) {
            queued = clean to done
            return
        }
        stop()
        stopFlag = false
        speaking = true
        val file = File(context.cacheDir, "sage_voice_${System.nanoTime()}.wav")
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}

            override fun onDone(utteranceId: String?) {
                Thread {
                    try {
                        play(file)
                    } catch (e: Exception) {
                    }
                    file.delete()
                    speaking = false
                    onLevel(0f)
                    main.post { done?.invoke() }
                }.start()
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                speaking = false
                main.post { done?.invoke() }
            }
        })
        tts.synthesizeToFile(clean, Bundle(), file, "sage")
    }

    fun stop() {
        stopFlag = true
    }

    fun shutdown() {
        stopFlag = true
        tts.shutdown()
    }

    /** Play a 16-bit PCM WAV file, reporting loudness (0..1) every ~30 ms. */
    private fun play(file: File) {
        val bytes = file.readBytes()
        val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        var channels = 1
        var rate = 22050
        var dataStart = -1
        var dataLen = 0
        var pos = 12
        while (pos + 8 <= bytes.size) {
            val id = String(bytes, pos, 4, Charsets.US_ASCII)
            val len = buf.getInt(pos + 4)
            if (id == "fmt ") {
                channels = buf.getShort(pos + 10).toInt()
                rate = buf.getInt(pos + 12)
            } else if (id == "data") {
                dataStart = pos + 8
                dataLen = minOf(len, bytes.size - dataStart)
                break
            }
            pos += 8 + len + (len and 1)
        }
        if (dataStart < 0) return
        val samples = ShortArray(dataLen / 2)
        buf.position(dataStart)
        buf.asShortBuffer().get(samples, 0, samples.size)

        val mask = if (channels == 2) AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO
        val minBuf = AudioTrack.getMinBufferSize(rate, mask, AudioFormat.ENCODING_PCM_16BIT)
        val track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(rate)
                .setChannelMask(mask).build())
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(minBuf, rate / 5))
            .build()
        track.play()
        val step = maxOf(1, rate * channels * 30 / 1000)     // 30 ms of samples
        var i = 0
        while (i < samples.size && !stopFlag) {
            val n = minOf(step, samples.size - i)
            var sum = 0.0
            for (k in i until i + n) {
                val s = samples[k] / 32768.0
                sum += s * s
            }
            val rms = sqrt(sum / n)
            val db = 20 * log10(maxOf(rms, 1e-7))
            onLevel(((db + 42) / 32).toFloat().coerceIn(0f, 1f))
            track.write(samples, i, n)
            i += n
        }
        track.stop()
        track.release()
    }
}

/** Listening, through the phone's speech recognizer (not our own placeholder service). */
class Ears(
    private val context: Context,
    private val onLevel: (Float) -> Unit,
    private val onPartial: (String) -> Unit,
    private val onResult: (String?) -> Unit,
) {
    private var recognizer: SpeechRecognizer? = null
    var listening = false
        private set

    private fun create(): SpeechRecognizer {
        val pm = context.packageManager
        val services = pm.queryIntentServices(Intent(RecognitionService.SERVICE_INTERFACE), 0)
            .filter { it.serviceInfo.packageName != context.packageName }
        val pick = services.firstOrNull { it.serviceInfo.packageName == "com.google.android.googlequicksearchbox" }
            ?: services.firstOrNull { it.serviceInfo.packageName.startsWith("com.google") }
            ?: services.firstOrNull()
        if (pick != null) {
            return SpeechRecognizer.createSpeechRecognizer(context,
                ComponentName(pick.serviceInfo.packageName, pick.serviceInfo.name))
        }
        if (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) {
            return SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        }
        return SpeechRecognizer.createSpeechRecognizer(context)
    }

    fun start() {
        recognizer?.destroy()
        val r = create()
        recognizer = r
        r.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {
                onLevel(((rmsdB + 2f) / 12f).coerceIn(0f, 1f))
            }
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {
                onLevel(0f)
            }
            override fun onError(error: Int) {
                listening = false
                onLevel(0f)
                onResult(null)
            }
            override fun onResults(results: Bundle?) {
                listening = false
                onLevel(0f)
                onResult(results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull())
            }
            override fun onPartialResults(partialResults: Bundle?) {
                partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                    ?.let(onPartial)
            }
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
            .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
        listening = true
        r.startListening(intent)
    }

    fun stop() {
        recognizer?.stopListening()
    }

    fun destroy() {
        recognizer?.destroy()
        recognizer = null
    }
}
