# opencode_study

## 環境設定

### 安裝 uv（第一次）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 建立虛擬環境並安裝套件

```bash
uv venv
uv pip install -r requirements.txt
```

### 進入虛擬環境

```bash
source .venv/bin/activate
```

### 離開虛擬環境

```bash
deactivate
```

## API Key 設定

複製 `.env` 並填入你的 API key：

```bash
cp .env.example .env   # 若有範本
```

或直接編輯 `.env`：

```
OPENAI_API_KEY=sk-...
```

## 執行範例

進入環境後，在 VS Code / Jupyter 開啟 `test/test.ipynb`，或用指令執行：

```bash
jupyter nbconvert --to notebook --execute test/test.ipynb --output test/test_out.ipynb
```
