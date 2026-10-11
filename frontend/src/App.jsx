import React, { useState, useRef } from 'react';
import axios from 'axios';
import { Mic, Square, Play, Type, FileAudio, Upload } from 'lucide-react';
import './index.css';

const API_URL = 'http://localhost:8000';

const ASR_LANGUAGES = {
  'AIHubSN/M-Kiriku-ASR': [
    { code: 'wo', label: 'Wolof' },
    { code: 'pu', label: 'Pulaar' },
    { code: 'se', label: 'Sérère' },
  ],
  'AIHubSN/Kiriku-Wolof-ASR': null,
};

// Decode any audio the browser can read (recording, mp3, m4a...) and re-encode it as 16 kHz mono WAV,
// so the backend does not need ffmpeg.
async function toWav16k(blob) {
  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  await ctx.close();

  const sampleRate = 16000;
  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * sampleRate), sampleRate);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();
  const samples = (await offline.startRendering()).getChannelData(0);

  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (off, str) => [...str].forEach((c, i) => view.setUint8(off + i, c.charCodeAt(0)));
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((v, i) => {
    const x = Math.max(-1, Math.min(1, v));
    view.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  });
  return new Blob([view], { type: 'audio/wav' });
}

async function apiError(error) {
  let detail = error.response?.data?.detail;
  if (error.response?.data instanceof Blob) {
    try { detail = JSON.parse(await error.response.data.text()).detail; } catch { /* not JSON */ }
  }
  return detail || 'Vérifiez que le backend FastAPI est lancé.';
}

function App() {
  const [activeTab, setActiveTab] = useState('tts');
  
  const [ttsText, setTtsText] = useState('');
  const [ttsModel, setTtsModel] = useState('AIHubSN/Kiriku-Wolof-TTS');
  const [ttsAudioUrl, setTtsAudioUrl] = useState(null);
  const [ttsLoading, setTtsLoading] = useState(false);

  const [asrModel, setAsrModel] = useState('AIHubSN/M-Kiriku-ASR');
  const [asrLanguage, setAsrLanguage] = useState('wo');
  const [isRecording, setIsRecording] = useState(false);
  const [asrResult, setAsrResult] = useState('');
  const [asrLoading, setAsrLoading] = useState(false);
  
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  const handleTtsSubmit = async (e) => {
    e.preventDefault();
    if (!ttsText.trim()) return;
    
    setTtsLoading(true);
    setTtsAudioUrl(null);
    
    try {
      const formData = new FormData();
      formData.append('text', ttsText);
      formData.append('model', ttsModel);

      const response = await axios.post(`${API_URL}/tts`, formData, {
        responseType: 'blob'
      });
      
      const url = URL.createObjectURL(response.data);
      setTtsAudioUrl(url);
    } catch (error) {
      console.error('Error generating audio:', error);
      alert(`Erreur lors de la génération de l'audio : ${await apiError(error)}`);
    } finally {
      setTtsLoading(false);
    }
  };

  const toggleRecording = async () => {
    if (isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    } else {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorderRef.current = new MediaRecorder(stream);
        audioChunksRef.current = [];

        mediaRecorderRef.current.ondataavailable = (e) => {
          if (e.data.size > 0) {
            audioChunksRef.current.push(e.data);
          }
        };

        mediaRecorderRef.current.onstop = async () => {
          stream.getTracks().forEach(track => track.stop());
          const audioBlob = new Blob(audioChunksRef.current, { type: mediaRecorderRef.current.mimeType });
          await processAsrAudio(audioBlob);
        };

        mediaRecorderRef.current.start();
        setIsRecording(true);
        setAsrResult('');
      } catch (err) {
        console.error("Error accessing microphone:", err);
        alert("Impossible d'accéder au microphone.");
      }
    }
  };

  const processAsrAudio = async (audioBlob) => {
    setAsrLoading(true);
    setAsrResult('');
    try {
      const formData = new FormData();
      formData.append('audio', await toWav16k(audioBlob), 'audio.wav');
      formData.append('model', asrModel);
      if (ASR_LANGUAGES[asrModel]) formData.append('language', asrLanguage);

      const response = await axios.post(`${API_URL}/asr`, formData);
      setAsrResult(response.data.text);
    } catch (error) {
      console.error('Error recognizing speech:', error);
      alert(`Erreur lors de la transcription : ${await apiError(error)}`);
    } finally {
      setAsrLoading(false);
    }
  };

  return (
    <div className="app-container">
      <header className="header">
        <h1>Kiriku Models Studio</h1>
        <p>Explorez les modèles d'IA vocale pour les langues sénégalaises</p>
      </header>

      <div className="tabs">
        <button 
          className={`tab-btn ${activeTab === 'tts' ? 'active' : ''}`}
          onClick={() => setActiveTab('tts')}
        >
          <Type size={20} /> Text-to-Speech
        </button>
        <button 
          className={`tab-btn ${activeTab === 'asr' ? 'active' : ''}`}
          onClick={() => setActiveTab('asr')}
        >
          <FileAudio size={20} /> Speech-to-Text
        </button>
      </div>

      {activeTab === 'tts' ? (
        <div className="tab-content">
          <form onSubmit={handleTtsSubmit}>
            <div className="form-group">
              <label>Modèle TTS</label>
              <select 
                className="form-control" 
                value={ttsModel} 
                onChange={(e) => setTtsModel(e.target.value)}
              >
                <option value="AIHubSN/Kiriku-Wolof-TTS">Kiriku Wolof TTS</option>
                <option value="AIHubSN/Kiriku-Pulaar-TTS">Kiriku Pulaar TTS</option>
              </select>
            </div>
            
            <div className="form-group">
              <label>Texte à synthétiser</label>
              <textarea 
                className="form-control" 
                placeholder="Entrez votre texte ici..."
                value={ttsText}
                onChange={(e) => setTtsText(e.target.value)}
                required
              />
            </div>
            
            <button type="submit" className="btn-primary" disabled={ttsLoading}>
              {ttsLoading ? (
                <><div className="loading-spinner" /> Génération...</>
              ) : (
                <><Play size={20} /> Générer l'audio</>
              )}
            </button>
          </form>

          {ttsAudioUrl && (
            <div className="result-box">
              <h3>Audio généré :</h3>
              <audio src={ttsAudioUrl} controls autoPlay />
            </div>
          )}
        </div>
      ) : (
        <div className="tab-content">
          <div className="form-group">
            <label>Modèle ASR</label>
            <select 
              className="form-control" 
              value={asrModel} 
              onChange={(e) => setAsrModel(e.target.value)}
            >
              <option value="AIHubSN/M-Kiriku-ASR">M-Kiriku ASR (Multilingue)</option>
              <option value="AIHubSN/Kiriku-Wolof-ASR">Kiriku Wolof ASR</option>
            </select>
          </div>

          {ASR_LANGUAGES[asrModel] && (
            <div className="form-group">
              <label>Langue parlée</label>
              <select
                className="form-control"
                value={asrLanguage}
                onChange={(e) => setAsrLanguage(e.target.value)}
              >
                {ASR_LANGUAGES[asrModel].map(({ code, label }) => (
                  <option key={code} value={code}>{label}</option>
                ))}
              </select>
            </div>
          )}

          <div style={{ marginTop: '2rem', marginBottom: '2rem' }}>
            <button 
              className={`record-btn ${isRecording ? 'recording' : ''}`} 
              onClick={toggleRecording}
              disabled={asrLoading}
            >
              {isRecording ? (
                <><Square size={24} /> Arrêter l'enregistrement</>
              ) : (
                <><Mic size={24} /> Cliquez pour enregistrer</>
              )}
            </button>

            <label className="upload-btn">
              <Upload size={18} /> ou importer un fichier audio
              <input
                type="file"
                accept="audio/*"
                hidden
                disabled={asrLoading || isRecording}
                onChange={(e) => {
                  const file = e.target.files[0];
                  e.target.value = '';
                  if (file) processAsrAudio(file);
                }}
              />
            </label>
          </div>

          {asrLoading && (
            <div style={{ display: 'flex', justifyContent: 'center', gap: '10px', color: 'var(--text-muted)' }}>
              <div className="loading-spinner" style={{ borderColor: 'rgba(59, 130, 246, 0.2)', borderTopColor: 'var(--accent-1)' }} />
              Transcription en cours...
            </div>
          )}

          {asrResult && !asrLoading && (
            <div className="result-box">
              <h3>Texte transcrit :</h3>
              <p style={{ fontSize: '1.2rem', lineHeight: '1.6' }}>{asrResult}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default App;
