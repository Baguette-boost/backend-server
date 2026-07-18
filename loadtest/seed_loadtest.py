"""부하테스트용 시드 SQL 생성기.
  python3 seed_loadtest.py | docker exec -i baguetteboost-db mysql -uroot -prootpw baguetteboost_service

기본: 보호자 50명(id 900~949), 환자 200명(id 1000~1199).
locustfile 의 기본 범위(PERSON_ID 1000~1200, GUARDIAN 1~50, phone 0100000XXXX)와 맞춤.
DEBUG_MODE=true 전제 → password 평문 저장.
"""
N_GUARDIANS = 50
G_ID0 = 900              # 기존 데이터와 겹치지 않는 시작 id
N_PERSONS = 200
P_ID0 = 1000
PHONE_FMT = "0100000{:04d}"   # locust GUARDIAN_PHONE_FMT 와 동일
PASSWORD = "password123"

print("SET FOREIGN_KEY_CHECKS=0;")

# 보호자 (phone 은 1..N 인덱스로 생성 → locust GUARDIAN_MIN/MAX=1..50 과 매칭)
g_vals = []
for i in range(N_GUARDIANS):
    gid = G_ID0 + i
    phone = PHONE_FMT.format(i + 1)
    g_vals.append(f"({gid},'lt_guardian_{i+1}','{phone}','{PASSWORD}','','','')")
print("INSERT INTO guardians (id,name,phone,password,access_token,refresh_token,expo_token) VALUES")
print(",\n".join(g_vals) + "\nON DUPLICATE KEY UPDATE password=VALUES(password);")

# 환자 (guardian_id 를 시드 보호자에 라운드로빈 배정)
p_vals = []
for j in range(N_PERSONS):
    pid = P_ID0 + j
    gid = G_ID0 + (j % N_GUARDIANS)
    p_vals.append(
        f"({pid},'lt_person_{pid}',70,'lt_dev_{pid}','lt_tok_{pid}',1,0,0,0,0,{gid})"
    )
print("INSERT INTO tracked_persons "
      "(id,name,age,device_id,device_token,is_active,is_fall,is_wandering,"
      "wandering_enrolled,fall_pending,guardian_id) VALUES")
print(",\n".join(p_vals) + "\nON DUPLICATE KEY UPDATE name=VALUES(name);")

print("SET FOREIGN_KEY_CHECKS=1;")
