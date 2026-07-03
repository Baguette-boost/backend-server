import requests
import json

import os
from dotenv import load_dotenv

def test_expo_push_sandbox():
    load_dotenv()

    # 🔥 실제 스마트폰의 Expo Go 앱에서 발급받은 토큰 env에서 가져옴
    MY_EXPO_TOKEN = os.getenv('MY_EXPO_TOKEN')
    
    url = "https://exp.host/--/api/v2/push/send"
    
    payload = {
        "to": MY_EXPO_TOKEN,
        "sound": "default",
        "title": "🚨 샌드박스 테스트",
        "body": "이 팝업이 보인다면 폰과 Expo 서버 간의 통신이 정상입니다.",
        "priority": "high"
    }
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    print(f"[{MY_EXPO_TOKEN}] 로 푸시 알림 전송을 시도합니다...")
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        
        print("\n=== 📡 Expo 서버 응답 결과 ===")
        print(f"HTTP 상태 코드: {response.status_code}")
        
        # 보기 편하게 JSON 포맷팅 출력
        pretty_json = json.dumps(response.json(), indent=2, ensure_ascii=False)
        print(pretty_json)
        
        if response.status_code == 200:
            resp_data = response.json().get("data", {})
            if resp_data.get("status") == "error":
                print("\n❌ [실패] 기기 등록이 해제되었거나 토큰이 유효하지 않습니다.")
                print(f"상세 에러: {resp_data.get('details')}")
            else:
                print("\n✅ [성공] Expo 서버가 알림을 정상적으로 수락했습니다. 스마트폰을 확인하세요!")
                
    except Exception as e:
        print(f"\n🚨 치명적 에러 발생: {e}")

if __name__ == "__main__":
    # 얘만 단독으로 실행하면 됨
    test_expo_push_sandbox()