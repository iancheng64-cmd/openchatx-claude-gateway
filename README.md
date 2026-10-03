# 🌐 OpenChatX Claude Gateway

<p align="center">
  <img src="https://raw.githubusercontent.com/iancheng64-cmd/openchatx-claude-gateway/main/docs/banner.png" alt="OpenChatX Claude Gateway" width="80%" onerror="this.style.display='none'"/>
</p>

<p align="center">
  <b>Connect your local OpenChatX Agent Runtime to Anthropic Claude (Web & Mobile) via spec-compliant OAuth 2.1 Remote MCP.</b>
  <br>
  <i>Empower Claude Free, Pro, and Team accounts with full local machine autonomy — File I/O, Terminal Shell, Skills, and Computer Control.</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/OpenChatX-v1.0.0--beta.1-blue?style=flat-square" alt="OpenChatX"/>
  <img src="https://img.shields.io/badge/OAuth-2.1%20(DCR%20%2B%20PKCE)-green?style=flat-square" alt="OAuth 2.1"/>
  <img src="https://img.shields.io/badge/Claude-Custom%20Connectors-purple?style=flat-square" alt="Claude"/>
  <img src="https://img.shields.io/badge/Python-3.10%2B-brightgreen?style=flat-square" alt="Python"/>
  <img src="https://img.shields.io/badge/License-MIT-orange?style=flat-square" alt="License"/>
</p>

---

## 💡 專案簡介 (Introduction)

**OpenChatX** 是一款強大的開源本機 AI Agent 桌面應用程式（由 XiaoPuOuO 開發），原生專為搭配 ChatGPT（透過 OpenAI Secure MCP Tunnel）操作 macOS/Windows 本機環境而設計。

然而，許多人希望在 **Claude Web 網頁版、桌面版或 iPhone/iPad App** 上也能直接把 Claude 當成真正的本機 Agent 使用，但 Anthropic Claude 的自訂連接器（Custom Connectors）要求：
1. **公開 HTTPS 端點**（Anthropic 伺服器無法直接存取 `127.0.0.1`）。
2. **嚴格符合 RFC 規範的 OAuth 2.1 授權流程**（必須支援動態客戶端註冊 DCR 或 CIMD、PKCE S256、Protected Resource Metadata 以及互動式授權批准介面）。

**OpenChatX Claude Gateway** 就是為了解決這個需求而誕生的**無侵入式橋接閘道**。

> ⚠️ **重要說明：本專案完全沒有改動 OpenChatX App 本體！**  
> 你不需要重新編譯或重新打包任何 `.dmg` 檔，只要下載並運行官方原版 OpenChatX App，搭配本專案的輕量級閘道與穿透通道，即可讓 Claude 與 ChatGPT 同時共享本機同一套 OpenChatX Core Runtime！

---

## 🏗️ 系統架構 (Architecture)

```mermaid
flowchart TD
    subgraph Cloud [雲端平台]
        ChatGPT[ChatGPT Web / App]
        Claude[Claude Web / Mobile App]
    end

    subgraph Tunnel [公共安全通道]
        OpenAITunnel[OpenAI Secure MCP Tunnel]
        PublicIngress[Tailscale Funnel / Cloudflare Tunnel<br/>https://your-domain.ts.net]
    end

    subgraph LocalMac [您的本機電腦 (macOS)]
        subgraph Gateway [OpenChatX Claude Gateway (Port 8765)]
            OAuthServer[OAuth 2.1 Server<br/>DCR / PKCE / Consent UI]
            ReverseProxy[Streamable HTTP<br/>Reverse Proxy]
            Database[(gateway.db<br/>Encrypted Clients & Tokens)]
        end

        subgraph OpenChatXApp [原版 OpenChatX.app (Port 8001)]
            CoreRuntime[OpenChatX Core Runtime<br/>127.0.0.1:8001/mcp]
            Tools[26項本機工具<br/>Files / Shell / Terminal / Skills / Subagents]
        end
    end

    ChatGPT -->|Direct Tunnel| OpenAITunnel --> CoreRuntime
    Claude -->|Remote MCP & OAuth| PublicIngress --> ReverseProxy
    PublicIngress <-->|OAuth Handshake| OAuthServer
    OAuthServer <--> Database
    ReverseProxy -->|Loopback HTTP| CoreRuntime
    CoreRuntime --> Tools
```

---

## ✨ 核心特色 (Key Features)

* **零侵入性**：完全不修改、不重構官方 OpenChatX App，安全乾淨。
* **雙 Agent 同步共存 (Dual-Provider)**：ChatGPT 與 Claude 可同時連線到同一個 OpenChatX Runtime，互不排斥。
* **支援 Claude 免費版與付費版**：在網頁端（Chrome、Safari 等）或手機端均可直接掛載。
* **標準 OAuth 2.1 授權鏈路**：完整實作 RFC 7591（動態客戶端註冊 DCR）、RFC 7636（PKCE S256）、RFC 8707（Resource Indicators）與加密憑證儲存。
* **自動常駐後台**：內附 macOS LaunchAgent 範本，開機自動啟動，無需手動掛終端。
* **解鎖完整 26 項 Agent 工具**：包含檔案讀寫修改、正則搜尋、終端指令執行、專案管理、子代理調度與跨會話記憶。

---

## 🚀 3 分鐘快速上手 (Quick Start)

### 步驟 1：確認 OpenChatX App 正在運行
確保你的 Mac 已經安裝並開啟了官方版 **OpenChatX.app**（預設會在 `127.0.0.1:8001` 提供 MCP 服務）。

### 步驟 2：複製專案並執行自動安裝精靈
打開終端機執行：

```bash
# 1. Clone 專案
git clone https://github.com/iancheng64-cmd/openchatx-claude-gateway.git
cd openchatx-claude-gateway

# 2. 執行互動式安裝精靈
bash scripts/setup.sh
```

`setup.sh` 會自動為你：
* 建立 Python 虛擬環境並安裝所需依賴套件。
* 生成專屬的資料庫 Fernet 加密金鑰。
* 產生標準合法的 bcrypt 密碼雜湊（預設帳號：`openchatx`，密碼：`openchatx`）。
* 自動偵測並綁定公網穿透域名（Tailscale Funnel 或 Cloudflare Tunnel）。
* 生成 `config.yaml` 並可選自動註冊為 macOS 系統開機常駐服務。

---

### 步驟 3：設定公網穿透通道 (Public Ingress)

Claude 官方伺服器需要透過 HTTPS 訪問閘道。推薦以下兩種**完全免費**的方案之一：

#### 方案 A（強烈推薦）：Tailscale Funnel
如果你有使用 Tailscale，這是一鍵擁有專屬固定網址的最佳解法：
```bash
bash scripts/tunnel-tailscale.sh
```
執行後會得到類似 `https://your-macbook.sawfish-mirach.ts.net` 的固定網址，將其填入 `config.yaml` 中的 `server.public_url`。

#### 方案 B：Cloudflare Quick Tunnel
如果沒有 Tailscale，可使用 Cloudflare 免費快速穿透：
```bash
bash scripts/tunnel-cloudflare.sh
```
終端機會顯示一組臨時 `https://*.trycloudflare.com` 網址。

---

### 步驟 4：在 Claude 中新增 OpenChatX 連接器

1. 打開瀏覽器（Chrome、Safari 等），登入 [claude.ai](https://claude.ai)。
2. 點擊左下角頭像 ➔ **Settings** ➔ 側邊欄點選 **Connectors** ➔ 切換到 **Yours** 分頁。
3. 點擊右上角 **Add** 按鈕 ➔ 選擇 **Add custom connector**。
4. **Step 1 of 2**：
   * **Name**：`OpenChatX`
   * **MCP server URL**：填入你的公開網址加上 `/mcp`（例如 `https://your-machine.ts.net/mcp`）
   * 點擊 **Continue**。
5. **Step 2 of 2**：
   * **Authentication**：選擇 `Sign in now`（系統會自動 Detected）。
   * **OAuth client**：選擇 **`Register automatically (DCR)`**（重要：請選 DCR 自動註冊）。
   * 點擊 **Add**。
6. **OAuth 授權彈窗**：
   * 頁面會開啟閘道的登入畫面，輸入你在設定時建立的帳號密碼（預設為 `openchatx` / `openchatx`）。
   * 登入後點擊 **Approve**。
7. **完成**！回到 Connectors 列表，即可看到 **OpenChatX** 顯示為綠色的 **Connected**！

---

## 💬 如何在對話中呼叫 OpenChatX？

1. 在 Claude 任何對話輸入框下方，點擊 **`+`（Add files, connectors, and more）**。
2. 點選 **Connectors** ➔ 勾選 **OpenChatX**。
3. 直接以自然語言對 Claude 下達指令，例如：
   * *「請使用 OpenChatX 列出我的 ~/Downloads 最新下載的 5 個檔案」*
   * *「請幫我打開專案目錄，搜尋含有特定函式的所有檔案並執行測試」*
   * *「請幫我在本地建立一個 Python 腳本並執行驗證」*

---

## 🛠️ 支援的 26 項本機 Agent 工具清單

| 分類 | 工具名稱 (Tool ID) | 功能簡述 |
| :--- | :--- | :--- |
| **檔案操作** | `openchatx_file_read` | 讀取指定路徑的檔案內容 |
| | `openchatx_file_write` | 新增檔案或完整寫入檔案 |
| | `openchatx_file_edit` | 外科手術式精確局部修改代碼 |
| | `openchatx_apply_patch` | 套用多檔案 diff/patch 補丁 |
| **檔案搜尋** | `openchatx_glob` | 根據 pattern 比對搜尋檔名與路徑 |
| | `openchatx_grep` | 正則表達式搜尋檔案內容 |
| | `openchatx_image_view` | 檢視本機圖片或截圖 |
| **系統執行** | `openchatx_bash` | 在本機終端機中執行 Shell 命令 |
| | `openchatx_bash_process` | 監控或管理長時間執行的後台進程 |
| | `openchatx_terminal` | 互動式虛擬終端與 REPL 連線 |
| **技能與擴充** | `openchatx_skill_search` | 搜尋 OpenChatX 內建與自訂 Skills |
| | `openchatx_skill_manage` | 新增、編輯或維護 Skill 工作流 |
| | `openchatx_capability_list` | 檢視目前已啟用的能力與擴充模組 |
| **工作區管理** | `openchatx_project_manage` | 設定與切換作用中的專案根目錄 |
| | `openchatx_goal_list` | 檢視當前工作區尚未完成的 Goals |
| | `openchatx_goal_manage` | 新增、更新或標記任務目標狀態 |
| | `openchatx_rule_resolve` | 解析與載入適用於當前任務的規則 (Rules) |
| | `openchatx_rule_manage` | 管理系統自訂約束規則 |
| **進階調度** | `openchatx_subagent_list` | 列出可調用的專屬子代理模型清單 |
| | `openchatx_subagent_run` | 啟動獨立子代理執行特定分支任務 |
| | `openchatx_summarize` | 提取會話核心脈絡，進行跨對話記憶交接 |
| | `openchatx_tool_search` | 動態檢索並懶載入本機與擴充工具庫 |
| | `openchatx_tool_call` | 呼叫動態掛載的內部工具 |
| | `openchatx_fetch_url` | 抓取外部網頁內容並轉為 Markdown |
| | `openchatx_start_here` | 每次新對話開始時自動對齊環境上下文 |
| | `gateway_status` | 查詢閘道即時運作狀態與健康度 |

---

## 🔍 常見問題與踩坑排查 (Troubleshooting & FAQs)

<details>
<summary><b>1. 為什麼點擊 Claude 的 Add 按鈕沒有反應？</b></summary>

在部分 Mac 筆記型電腦螢幕解析度下，Claude 的自訂連接器彈窗高度可能超出視窗可視高度，導致下方的 `Add` 按鈕剛好落在可視範圍邊緣以下。請利用滑鼠滾輪或兩指滑動彈窗內容至最底，確認完整看見 `Back` 與 `Add` 按鈕後再行點擊。
</details>

<details>
<summary><b>2. 登入閘道時出現「Invalid username or password」？</b></summary>

請勿手動在 `config.yaml` 裡憑空捏造類似 bcrypt 的字串（隨機字串會導致 Python bcrypt 噴出 `ValueError: Invalid salt` 並必定拒絕登入）。請使用專案內附的腳本重新生成合法密碼雜湊：
```bash
python3 scripts/hash_password.py 你的新密碼
```
並將輸出的字串貼入 `config.yaml` 中的 `password_hash`。
</details>

<details>
<summary><b>3. Claude 出現「Client ID not found (400 Bad Request)」？</b></summary>

這是因為 Claude 之前透過 DCR 註冊的 Client ID 儲存在舊資料庫中，而目前的閘道讀取了另一個空白的資料庫檔案。請確保 `config.yaml` 裡的 `storage.path` 始終指向同一個持久化檔案（例如 `./gateway.db`），切勿隨意刪除或切換路徑。
</details>

<details>
<summary><b>4. 如何檢查所有服務是否都在正常運作？</b></summary>

隨時在專案目錄執行健康檢查腳本：
```bash
bash scripts/status.sh
```
腳本會一次檢驗：
1. OpenChatX 本機核心端點 (`127.0.0.1:8001`)
2. 閘道伺服器本機端點 (`127.0.0.1:8765`)
3. 公共 HTTPS 穿透網址連通性
4. macOS 系統開機常駐服務 (LaunchAgent) 狀態
</details>

---

## 🔒 安全性考量 (Security Model)

* **迴圈保護 (Loopback Isolation)**：本地 OpenChatX Runtime (`127.0.0.1:8001`) 僅傾聽本機迴路，絕不直接暴露於公共網際網路。
* **靜態加密 (At-Rest Encryption)**：所有動態註冊的 OAuth Client、Access Token 與 Refresh Token 均使用 AES-256 (Fernet) 高度加密保存於本機 SQLite 資料庫中。
* **規範級 OAuth 2.1**：實作 PKCE (Proof Key for Code Exchange) S256 與狀態防偽權杖（State Parameter），杜絕授權碼攔截與 CSRF 攻擊。

---

## 📄 開源授權 (License)

本專案採用 [MIT License](LICENSE) 開源授權。
原 OpenChatX App 著作權歸原作者所有。
