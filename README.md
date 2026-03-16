# OmniSearch

**The Local-First, AI-Powered Search Engine for Your Personal Knowledge Base.**

OmniSearch is a premium, privacy-centric desktop application designed to bridge the gap between your local files and modern AI. It combines the speed of local indexing with the intelligence of Large Language Models to provide a "Google-like" experience for your own computer—without sacrificing privacy.

![OmniSearch Header](https://raw.githubusercontent.com/lucide-react/lucide/main/icons/sparkles.svg) <!-- Replace with actual banner if available -->

---

## ✨ Features

### 🔍 Multimodal Semantic Search
Stop hunting for filenames. Powered by **Google Gemini Embedding 2**, OmniSearch understands more than just text. It performs true **Multimodal Search**, allowing you to find content across different formats in a unified vector space.
- **Beyond Text**: Find images, audio, and video files based on their content and context, not just their metadata.
- **Hybrid Engine**: Combines state-of-the-art **Vector Embeddings** with **BM25 Lexical Search** for pinpoint accuracy.
- **Intelligent Reranking**: Integrated BGE-reranker ensures the most relevant results—whether text or media—appear at the top.
- **Deep Parsing**: Native support for PDFs, Markdown, Word docs, images, and more.

### 🤖 AI Copilot & Agents
- **Context-Aware Q&A**: Chat with your local knowledge base. The Copilot uses RAG (Retrieval-Augmented Generation) to answer questions based *only* on your files.
- **Autonomous Agents**: Auto-routed task execution for complex workflows (e.g., summarizing folders, finding specific meeting notes, or organizing data).

### ☁️ Encrypted Cloud Sync
Seamlessly sync your index across devices using Cloudflare R2.
- **Security-First**: All data is AES-256-GCM encrypted locally before upload. Your encryption key never leaves your machine.
- **Delta Snapshots**: Only uploads changed data, keeping syncs fast and efficient.

### 🖥️ Premium Desktop Experience
Built with **Tauri + React**, OmniSearch feels like a native utility with a modern, high-performance UI.
- **Glassmorphism Design**: A sleek, dark-themed interface optimized for focus.
- **Global Shortcuts**: Hit `⌘K` for Copilot, `⌘J` for Agents, or `⌘,` for settings—instantly.

---

## 🛠️ Tech Stack

- **Frontend**: [React](https://reactjs.org/), [TypeScript](https://www.typescriptlang.org/), [Tailwind CSS](https://tailwindcss.com/), [Vite](https://vitejs.dev/).
- **Desktop Bridge**: [Tauri](https://tauri.app/).
- **Backend**: Python 3.10+, [Qdrant](https://qdrant.tech/) (Vector DB), [SQLite](https://www.sqlite.org/index.html) (Metadata).
- **AI Models**: **Google Gemini Embedding 2** (Multimodal text/image/video embeddings), **Gemini 2.0 Flash** (LLM for Copilot), and **BGE-Reranker**.
- **Cloud**: Cloudflare R2 (Storage).

---

## 🚀 Getting Started

### Prerequisites
- **Node.js**: v18+
- **Python**: 3.10+
- **Gemini API Key**: Obtain from [Google AI Studio](https://aistudio.google.com/).

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/omnisearch.git
   cd omnisearch
   ```

2. **Setup Backend**:
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Setup Frontend**:
   ```bash
   cd ../frontend
   npm install
   ```

### Configuration

Create a `.env` file in the `backend/` directory:
```env
# AI Provider
GOOGLE_API_KEY=your_gemini_api_key

# Cloud Sync (Optional)
R2_ACCOUNT_ID=your_id
R2_ACCESS_KEY=your_key
R2_SECRET_KEY=your_secret
R2_BUCKET_NAME=omnisearch
ENABLE_CLOUD_SYNC=true
```

### Running OmniSearch

1. **Start the Brain (Backend)**:
   ```bash
   cd backend
   python brain.py
   ```

2. **Start the UI (Development)**:
   ```bash
   cd frontend
   npm run tauri dev
   ```

---

## 🛡️ Architecture & Privacy

OmniSearch follows a **Local-Primary** philosophy.
- Your files are **never** uploaded to the cloud.
- Only the search index (mathematical representations of your data) and metadata are ever synced, and even then, they are fully encrypted.
- SQLite is used for robust metadata tracking, allowing for index rebuilds without re-processing files.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<p align="center">Made with ❤️ for the future of personal knowledge management.</p>
