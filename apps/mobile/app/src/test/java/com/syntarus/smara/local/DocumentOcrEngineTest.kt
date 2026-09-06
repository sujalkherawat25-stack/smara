package com.syntarus.smara.local

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DocumentOcrEngineTest {
    @Test
    fun rejectsNoiseAndEmptyScans() {
        assertFalse(DocumentOcrEngine.isUsefulText(""))
        assertFalse(DocumentOcrEngine.isUsefulText("Page 1"))
        assertFalse(DocumentOcrEngine.isUsefulText("..... ----"))
    }

    @Test
    fun acceptsSubstantialLatinAndDevanagariText() {
        assertTrue(DocumentOcrEngine.isUsefulText("Candidate: Sujal Kherawat\nExperience: Built production memory infrastructure for AI agents.\nSkills: Kotlin, Python, distributed systems."))
        assertTrue(DocumentOcrEngine.isUsefulText("\u092F\u0939 \u0926\u0938\u094D\u0924\u093E\u0935\u0947\u091C \u0938\u094D\u0925\u093E\u0928\u0940\u092F OCR \u0915\u0940 \u0917\u0941\u0923\u0935\u0924\u094D\u0924\u093E \u091C\u093E\u0901\u091A\u0928\u0947 \u0915\u0947 \u0932\u093F\u090F \u092A\u0930\u094D\u092F\u093E\u092A\u094D\u0924 \u0939\u093F\u0928\u094D\u0926\u0940 \u092A\u093E\u0920 \u0930\u0916\u0924\u093E \u0939\u0948\u0964 \u0907\u0938\u092E\u0947\u0902 \u0928\u093E\u092E, \u0924\u093E\u0930\u0940\u0916, \u0905\u0928\u0941\u092D\u0935 \u0914\u0930 \u0906\u0935\u0936\u094D\u092F\u0915 \u0935\u093F\u0935\u0930\u0923 \u0936\u093E\u092E\u093F\u0932 \u0939\u0948\u0902\u0964"))
    }
}
