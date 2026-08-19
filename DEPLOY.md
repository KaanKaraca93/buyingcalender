# Deploy — GitHub → Heroku → ION API Gateway

**Tek repo, iki ortam.** Kod TST ve PRD'de birebir aynidir; ortami yalnizca
Heroku Config Var'lari ayirir. Yani:

| | Repo | Heroku app | `IONAPI_B64` | ION API kaydi |
|---|---|---|---|---|
| TST | `buyingcalender` (main) | `...-tst` | TST `.ionapi` | TST tenant'inda `TAKVIMAPI` |
| PRD | ayni repo, ayni branch | `...-prd` | PRD `.ionapi` | PRD tenant'inda `TAKVIMAPI` |

PRD'ye gecerken kod tarafinda hicbir sey degismez: ikinci bir Heroku app'i acilir,
ayni GitHub repo'suna baglanir, config var'lari PRD degerleriyle doldurulur.
Ilk kurulum **TST** (`ATJZAMEWEF5P4SNV_TST`) icindir.

---

## 1. Repo

**Tek gecerli repo: [`KaanKaraca93/buyingcalender`](https://github.com/KaanKaraca93/buyingcalender)**

Kod bu repoda yasar; Heroku bu repoya baglanir. Muzaffer'in hesabindaki
`muzafferkaya-ui/m3-takvim-api` yalnizca ilk yedektir, **kullanilmaz** —
oraya yapilan push Heroku'ya hicbir sey deploy etmez.

> ⚠️ Repo **Private** olmali. Kod icinde credential yok ama M3 tablo semalari,
> PLM entity/rol isimleri ve tenant kimligi (`ATJZAMEWEF5P4SNV_TST`) iceriyor.
> Settings → General → Danger Zone → *Change repository visibility* → Private.

Kod guncellemesi (git ile):

```bash
cd m3-takvim-api
git remote add origin https://github.com/KaanKaraca93/buyingcalender.git   # ilk seferde
git branch -M main
git push -u origin main
```

Git kurulu degilse repo sayfasindaki **"uploading an existing file"** ile
tarayicidan da yuklenebilir. Dikkat: tarayici yukleyicisi noktayla baslayan
dosyalari (`.gitignore`, `.env.example`) atlar; onlar **Add file → Create new file**
ile elle olusturulur.

> `.gitignore` `*.ionapi` ve `.env`'i disarida tutuyor. Push oncesi `git status`
> ile hicbir credential dosyasinin listede olmadigini dogrula.

---

## 2. Heroku app (Kaan)

1. **New → Create new app** → ad + **Europe** bölgesi (M3 EU1'de).
2. **Deploy** sekmesi → *Deployment method* → **GitHub** → hesabı bağla →
   `buyingcalender` → **Connect**.
3. **Manual deploy** → branch `main` → **Deploy Branch**.
   (İstenirse *Enable Automatic Deploys*.)
4. Buildpack otomatik `heroku/python` (repo'da `requirements.txt` + `runtime.txt` var).
5. **Resources** → dyno **Basic** (Eco 30 dk sonra uyur, ilk istek ~10 sn gecikir).

---

## 3. Config Vars (Heroku → Settings → Reveal Config Vars)

| Key | Değer | Not |
|-----|-------|-----|
| `IONAPI_B64` | *(Muzaffer girer)* | `credentials.ionapi` (TST) dosyasının base64'ü |
| `AUTH_MODE` | `gateway` | ION Gateway arkasında çalışacak |
| `API_KEY` | rastgele uzun bir dize | **Zorunlu.** ION API kaydında sabit header olarak verilecek |
| `ENFORCE_ROLE_POLICY` | `0` | Yetki widget'larda kalır |
| `ALLOW_DELETE` | `0` | |
| `MAX_ROWS_PER_REQUEST` | `50` | |
| `REQUEST_DEADLINE_SECONDS` | `20` | Heroku 30 sn'de keser |
| `WRITE_CONCURRENCY` | `6` | |
| `READ_ONLY` | `0` | Acil durumda `1` → tüm yazmalar kapanır |
| `LOG_LEVEL` | `INFO` | |

`.ionapi` → base64:

```powershell
# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.ionapi"))
```

`API_KEY` üretmek için:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> **Not:** Infor'dan indirilen `ServiceAccount_*.csv` tek başına yetmez — içinde
> yalnızca Access Key / Secret Key var. `ci`, `cs`, `iu`, `pu`, `ot`, `ti` alanları
> ION API → Authorized Apps → *Download credentials* ile alınan `.ionapi`
> dosyasından gelir. `m3DB/credentials.ionapi` (uygulama adı `BackendServisi`,
> tenant `ATJZAMEWEF5P4SNV_TST`) bu iş için hazır durumda.

---

## 4. Deploy sonrası doğrulama

```bash
curl https://<app>.herokuapp.com/healthz
# {"status":"ok","version":"0.1.0"}

curl https://<app>.herokuapp.com/readyz
# {"status":"ready","ion":"ok"}     ← ION token alınabiliyor

curl -X POST https://<app>.herokuapp.com/v1/m3/list \
     -H "Content-Type: application/json" \
     -d '{"file":"CPSTAKVIM","keys":{"PK01":"11"}}'
# 401  ← anahtarsız erişim kapalı olmalı. 200 dönüyorsa API_KEY tanımlanmamıştır!
```

`readyz` 503 dönerse: `IONAPI_B64` yanlış ya da ION tarafında servis hesabı/IP
sorunu var. Heroku → **More → View logs** ilk bakılacak yer.

---

## 5. ION API Gateway'e tanıtma

Adım adım: [`ion/README.md`](ion/README.md). Servis hesabıyla token alma akışının
tam anlatımı: [`ion/TOKEN.md`](ion/TOKEN.md). Özetle:

1. `ion/swagger-2.0.json` içindeki `"host"` alanını Heroku adresiyle değiştir.
2. ION API → Available APIs → Add → dosyayı yükle, adı `TAKVIMAPI` yap.
3. Target endpoint: `https://<app>.herokuapp.com`
4. Backend header: `X-Api-Key: <Heroku API_KEY>`
5. Widget'ların kullandığı Authorized App'e bu API için erişim ver.

---

## 6. Sonraki adım (Muzaffer)

`/healthz` ve `/readyz` yeşil olduktan sonra widget tarafı taşınacak:
`M3_EXEC` ile yapılan `executeIonApiAsync` çağrıları `TAKVIMAPI/v1/m3/exec`'e
dönüşecek. Yanıt gövdesi aynı olduğu için `_parseM3` ve alan eşlemeleri
değişmiyor; **rol/yetki kodlarına hiç dokunulmuyor**. PLM OData çağrıları
(Style, Season, CriticalPath2, USER, IDM) da aynen kalıyor.
