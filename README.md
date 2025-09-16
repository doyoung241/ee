# 📚 Streamlit Exam Generator — Wizard UI

랜딩 → 업로드(다중 PDF) → 문제 생성 → 풀이(한 화면) → 제출 → 채점 & 모범답안/출처 → 약점 리포트 → 이용내역

## 실행
```bash
python -m venv venv
# mac/linux
source venv/bin/activate
# windows powershell
venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY 입력
streamlit run app.py
```
