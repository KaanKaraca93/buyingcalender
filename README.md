# Buying Takvim — M3 Proxy API

> **Repo:** [`KaanKaraca93/buyingcalender`](https://github.com/KaanKaraca93/buyingcalender) — Heroku bu repoya baglidir, kod guncellemeleri
> buraya gonderilir. Baska bir kopyaya yapilan push deploy tetiklemez.

PLM takvim widget'ları (`koleksiyon-oncesi-takvim`, `koleksiyon-ici-takvim`,
`m3-takvim-excel`) bugün M3'e **doğrudan** gidiyor:

```
GET M3/m3api-rest/execute/CUSEXTMI/LstFieldValue;maxrecs=300?FILE=CPSTAKVIM&PK01=11
```

Çağrı `widgetContext.executeIonApiAsync` ile **kullanıcının kendi yetkisiyle**
yapılıyor — dolayısıyla widget'ı kullanan herkesin M3 erişimi olması gerekiyor.
Herkeste yok.

Bu servis araya girer ve M3'e **servis hesabıyla** gider:

```
Widget ──▶ ION API Gateway (custom API) ──▶ Bu API ──(servis hesabı)──▶ M3 CUSEXTMI
```

Kullanıcının M3 yetkisi olmasına gerek kalmaz; PLM oturumu yeterlidir.

> **Ortam:** İlk kurulum TST (`ATJZAMEWEF5P4SNV_TST`) içindir. PRD için aynı repo
> ikinci bir Heroku app'ine farklı `IONAPI_B64` ile deploy edilir.

---

## Tasarım ilkesi — yetkilendirme nerede

**Bu API yetkilendirme yapmaz.** Widget'lardaki rol/departman mantığı olduğu gibi
kalır; API yalnızca widget'ın M3 çağrısının yerine geçer, davranışını değiştirmez.
`ENFORCE_ROLE_POLICY` varsayılan olarak **kapalıdır** — widget'ın yetki kontrolünden
geçen her rol bu API'yi çağırabilir.

Sunucunun yaptığı üç şey var, hiçbiri widget davranışını değiştirmez:

1. **Kapı** — İstekler yalnızca ION API Gateway üzerinden gelir; gateway'in
   eklediği `X-Api-Key` yoksa 401. Heroku URL'i internete açık kalmaz.
2. **Kapsam** — Genel amaçlı pass-through değil. Sadece `KOLONCESI`, `CPSTAKVIM`,
   `TEMATAKVIM` (ve `EXTRA_FILES` ile eklenenler); sadece tanımlı PK ve alanlar;
   `Lst/Get/Add/Chg`. `DelFieldValue` varsayılan kapalı (`ALLOW_DELETE=1` ile açılır).
3. **İz** — Her çağrı JSON denetim satırı olarak stdout'a düşer.

`policy.py` içindeki rol kuralları (widget'ın `ROLE_RULES`'undan birebir alınmış)
ileride sunucu tarafında kontrol istenirse diye hazır durur; kendiliğinden devreye girmez.

---

## Endpoint'ler

| Metod | Yol | Açıklama |
|-------|-----|----------|
| GET | `/healthz` | Anahtar gerektirmez; ayakta mı |
| GET | `/readyz` | ION token alınabiliyor mu |
| GET | `/v1/me` | Çağıranın kimliği (bilgilendirme) |
| GET | `/v1/tables` | İzin listesi ve aktif ayarlar |
| POST | **`/v1/m3/exec`** | **M3'e birebir geçiş — widget migrasyonu için** |
| POST | `/v1/m3/list` | `LstFieldValue`, sadeleştirilmiş |
| POST | `/v1/m3/upsert` | Çoklu satır upsert |

### `/v1/m3/exec` — widget'ı en az değiştiren yol

```jsonc
POST /v1/m3/exec
{ "transaction": "LstFieldValue", "maxrecs": 300,
  "params": { "FILE": "CPSTAKVIM", "PK01": "11", "PK02": "PROD" } }
```

Yanıt M3'ün **ham gövdesi**:

```jsonc
{ "MIRecord": [ { "NameValue": [ { "Name": "A130", "Value": "2026-05-01" } ] } ] }
```

Bu yüzden widget'lardaki `_parseM3(resp)` ve alan eşleme mantığı değişmeden çalışır.
Yazma için de aynı uç: `{"transaction":"ChgFieldValue","params":{"FILE":"KOLONCESI","PK01":…,"A230":"2026-03-12"}}`.

Widget tarafındaki tam karşılığı: [`ion/README.md`](ion/README.md).

### `/v1/m3/upsert` — toplu yazma (Excel yükleme)

```jsonc
{ "file": "KOLONCESI", "strategy": "chg_first",
  "rows": [ { "keys": { "PK01": "11", "…": "…", "PK07": "Makro_Trend_Sunum" },
              "values": { "A230": "2026-03-12" },
              "create_values": { "A121": "Makro Trend Sunum", "A330": "Tasarım" } } ] }
```

- `chg_first` (varsayılan) → önce `ChgFieldValue`, "kayıt yok" hatasında `AddFieldValue`.
  Tek hücre düzenlemede doğrusu budur; diğer alanlar ezilmez.
- `add_first` → önce `AddFieldValue`, "already exist" hatasında `ChgFieldValue`.
  Excel'den toplu ilk yüklemede daha az çağrı.

`remaining` doluysa süre sınırına takılan satırlar var; istemci onları yeni bir
istekte gönderir.

---

## Heroku'ya özgü sınırlar

| Konu | Durum |
|------|-------|
| Router timeout | Heroku isteği **30 sn**'de keser (H12). `REQUEST_DEADLINE_SECONDS=20` dolunca kalan satırlar `remaining` ile bildirilir. |
| Parça boyutu | `MAX_ROWS_PER_REQUEST=50`; fazlası 413. Excel yüklemesi widget tarafında parçalanmalı. |
| Uyuyan dyno | Eco/Basic dyno 30 dk sonra uyur → ilk istek ~10 sn gecikir. Basic önerilir. |
| Statik IP | Heroku dyno IP'leri değişkendir. ION tarafında IP kısıtı varsa Fixie/QuotaGuard gerekir. |

---

## Yerel çalıştırma

```bash
pip install -r requirements.txt
cp .env.example .env          # IONAPI_B64'ü doldur
export AUTH_MODE=dev          # yalnızca yerelde! kimlik doğrulaması yapmaz
uvicorn app.main:app --reload
# http://localhost:8000/docs  (interaktif docs yalnızca dev modda)
```

```bash
python -m pytest -q           # 52 test
```

---

## Yapılandırma

Tüm ayarlar ortam değişkeni; sırlar repoda durmaz (`.gitignore` `*.ionapi` ve
`.env`'i dışarıda tutar). Tam liste `.env.example`'da. En kritikleri:

```
IONAPI_B64           credentials.ionapi dosyasının base64'ü
AUTH_MODE            gateway (varsayılan) | infor_token | gateway_jwt | dev
API_KEY              gateway modunda ZORUNLU — ION kaydında sabit header olarak verilir
ENFORCE_ROLE_POLICY  0 (varsayılan) — yetki widget'ta kalır
ALLOW_DELETE         0 (varsayılan) — DelFieldValue kapalı
EXTRA_FILES          ileride başka CUGEX tabloları gerekirse (virgülle)
```

---

## Dosya haritası

| Dosya | İşlev |
|-------|-------|
| `app/config.py` | Ortam değişkenleri + `.ionapi` çözümleme |
| `app/ion.py` | ION OAuth2 + `m3api-rest` istemcisi (async), ham/parse edilmiş yanıt |
| `app/auth.py` | Gateway anahtarı, opsiyonel kimlik çözümleme |
| `app/policy.py` | İzin listesi (+ pasif rol kuralları) |
| `app/service.py` | `list` / `upsert` iş mantığı, süre bütçesi, eşzamanlılık |
| `app/routes/m3.py` | HTTP endpoint'leri |
| `app/audit.py` | JSON denetim kaydı |
| `ion/` | ION API Gateway kaydı: Swagger 2.0 + adım adım talimat |
| `tests/` | 52 test |

## Netleşmesi gereken tek teknik nokta

`app/service.py` içindeki `NOT_FOUND_PATTERNS` / `ALREADY_EXISTS_PATTERNS` —
M3'ün hata mesajı metni servis hesabının diline göre değişebilir. İlk gerçek
yazma testinde dönen mesaj buraya eklenmeli; yanlış eşleşirse upsert fallback'i
çalışmaz (kayıt eklenmez ya da hata döner).
