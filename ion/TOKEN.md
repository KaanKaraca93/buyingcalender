# ION API'den token alma — servis hesabı akışı

Bu API M3'e **kullanıcı adına değil, servis hesabı adına** bağlanır. Her M3
çağrısından önce ION'dan bir OAuth2 access token alınır. Bu doküman o akışı
uçtan uca anlatır; kodda karşılığı `app/ion.py` içindeki `IonServiceClient`.

---

## 1. Nereden geliyor bu bilgiler

Infor iki ayrı şey verir ve **ikisi de gerekir**:

| Kaynak | İçindekiler | Nasıl alınır |
|--------|-------------|--------------|
| **`.ionapi` dosyası** | `ti`, `ci`, `cs`, `iu`, `pu`, `ot` | ION API → Authorized Apps → uygulamayı seç → *Download credentials* |
| **Servis hesabı** | `saak`, `sask` | Aynı ekranda servis hesabı seçilerek indirilir; ayrıca CSV olarak da verilir |

`.ionapi` dosyası bir JSON'dur ve genelde servis hesabı da içine gömülü gelir.
Alanların anlamı:

| Alan | Anlamı | Örnek |
|------|--------|-------|
| `ti` | Tenant id | `ATJZAMEWEF5P4SNV_TST` |
| `iu` | ION API gateway adresi | `https://mingle-ionapi.eu1.inforcloudsuite.com` |
| `pu` | SSO (token) sunucusu | `https://mingle-sso.eu1.inforcloudsuite.com/ATJZAMEWEF5P4SNV_TST/as/` |
| `ot` | Token endpoint yolu | `token.oauth2` |
| `ci` | Client id | `ATJZAMEWEF5P4SNV_TST~vlWkwz…` |
| `cs` | Client secret | *(gizli)* |
| `saak` | Service Account Access Key → OAuth **username** | `ATJZAMEWEF5P4SNV_TST#…` |
| `sask` | Service Account Secret Key → OAuth **password** | *(gizli)* |

Token URL = `pu` + `ot`, yani:

```
https://mingle-sso.eu1.inforcloudsuite.com/ATJZAMEWEF5P4SNV_TST/as/token.oauth2
```

> **Dikkat:** `ci`/`cs` **uygulamayı**, `saak`/`sask` **servis hesabını** temsil eder.
> Uygulama aynı kalırken servis hesabı değiştirilebilir (yenilenir/rotate edilir).
> Bu yüzden "client id aynı ama token alınamıyor" durumunda bakılacak yer
> `saak`/`sask` çiftidir.

---

## 2. Token isteği

`grant_type=password` kullanılır; kullanıcı adı/şifre yerine servis hesabı
anahtarları gönderilir.

```
POST https://mingle-sso.eu1.inforcloudsuite.com/{TENANT}/as/token.oauth2
Content-Type: application/x-www-form-urlencoded
Accept: application/json

grant_type=password
&username={saak}
&password={sask}
&client_id={ci}
&client_secret={cs}
```

### curl

```bash
curl -s -X POST "https://mingle-sso.eu1.inforcloudsuite.com/$TENANT/as/token.oauth2" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Accept: application/json" \
  --data-urlencode "grant_type=password" \
  --data-urlencode "username=$SAAK" \
  --data-urlencode "password=$SASK" \
  --data-urlencode "client_id=$CI" \
  --data-urlencode "client_secret=$CS"
```

### Yanıt

```json
{
  "access_token": "eyJraWQiOiJrZzpjNmE0ODgwZi02ZDI0…",
  "token_type": "Bearer",
  "expires_in": 7200
}
```

`expires_in` **7200 saniye = 2 saat**. Token bir JWT'dir; içindeki `aud`
(`https://mingle-ionapi.eu1.inforcloudsuite.com`) ve `Tenant` alanları
doğru tenant'a bağlandığını gösterir.

---

## 3. Token ile M3 çağrısı

```
GET {iu}/{ti}/M3/m3api-rest/execute/CUSEXTMI/LstFieldValue;maxrecs=300?FILE=CPSTAKVIM&PK01=11
Authorization: Bearer {access_token}
Accept: application/json
```

Üç tuzak (deneme yanılmayla öğrenilmiş, koda da yansıtılmıştır):

1. **`maxrecs` bir MATRIX parametresidir** — `LstFieldValue;maxrecs=300` şeklinde
   yola yazılır. `?maxrecs=300` olarak gönderilirse **yok sayılır** ve her zaman
   100 kayıt döner.
2. **Yazma transaction'ları da GET ile çağrılır.** `AddFieldValue` / `ChgFieldValue`
   POST ile denenirse `405 Method Not Allowed` alınır.
3. **HTTP 200 her zaman başarı demek değildir.** Gövdede `ErrorMessage` olabilir;
   iş katmanı hatası böyle döner.

---

## 4. Bu API bunu nasıl yapıyor

`app/ion.py` → `IonServiceClient`:

- Token'ı **bellekte cache'ler**, süresi dolmadan 30 sn önce yeniler.
  Yani her M3 çağrısında yeni token alınmaz — normal şartlarda 2 saatte bir alınır.
- Eşzamanlı isteklerde tek token alınsın diye `asyncio.Lock` kullanır.
- M3 `401` dönerse token'ı çöpe atıp **bir kez** yeniler ve isteği tekrarlar.
- `.ionapi` dosyasının tamamı `IONAPI_B64` ortam değişkeninden okunur
  (base64'lenmiş JSON) — dosya olarak repoda **durmaz**.

`IONAPI_B64` üretmek:

```powershell
# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.ionapi"))
```

```bash
# macOS / Linux
base64 -w0 credentials.ionapi
```

---

## 5. Deploy sonrası doğrulama

```bash
curl https://<app>.herokuapp.com/readyz
```

Yanıt her durumda hangi tenant'a, hangi token URL'ine bağlanmaya çalıştığını
da yazar — yanlış ortama gidiliyorsa oradan görülür.

| Yanıt | Anlamı |
|-------|--------|
| `{"status":"ready","ion":"ok", ...}` | Token alınabiliyor — credential'lar doğru |
| `503` + `"stage":"config"` | Credential hiç çözülemedi: değişken isimleri yanlış ya da eksik |
| `503` + `"stage":"token"` + `401/400` | Değişkenler okundu ama ION reddetti → `saak`/`sask` yanlış veya servis hesabı yenilenmiş |
| `"warnings": ["... API_KEY tanimli degil ..."]` | Servis çalışıyor ama `/v1/*` uçları 500 döner; `API_KEY` eklenmeli |

**Config var isimleri** — ikisinden biri:

| Yöntem | Değişkenler |
|--------|-------------|
| Tek değişken *(önerilen)* | `IONAPI_B64` |
| Tek tek | `ci`, `cs`, `saak`, `sask`, `sso` *(+ opsiyonel `ion`, `ti`)* |

`sso` **tam token URL'i** olmalı: `https://mingle-sso.eu1.inforcloudsuite.com/<TENANT>/as/token.oauth2`

Sonraki adım olarak gerçek bir okuma:

```bash
curl -X POST https://<app>.herokuapp.com/v1/m3/exec \
  -H "Content-Type: application/json" -H "X-Api-Key: <API_KEY>" \
  -d '{"transaction":"LstFieldValue","maxrecs":300,
       "params":{"FILE":"CPSTAKVIM","PK01":"11"}}'
```

`{"MIRecord":[…]}` dönüyorsa zincirin tamamı çalışıyor demektir.

---

## 6. Anahtar yenileme (rotate)

Servis hesabı anahtarları Infor tarafında yenilenirse **tek yapılacak şey**
Heroku'daki `IONAPI_B64` değerini yeni `.ionapi` ile güncellemektir. Kod
değişmez, deploy gerekmez — Heroku config var değişiminde dyno kendini
yeniden başlatır ve yeni anahtarla token alır.

> Credential değerleri bu repoda **tutulmaz**. Repo Heroku'ya bağlı olduğu ve
> ileride başkalarıyla paylaşılabileceği için sırlar yalnızca Heroku Config
> Vars'ta yaşar. Değerler Kaan'a ayrı kanaldan iletilir.
