# ION API Gateway'e custom API olarak tanıtma

> Repo: [`KaanKaraca93/buyingcalender`](https://github.com/KaanKaraca93/buyingcalender)
> · Servis hesabıyla token alma akışı: [`TOKEN.md`](TOKEN.md)

Amaç: widget'lar Heroku URL'ine **doğrudan** gitmesin. ION API Gateway arkasında
dursun ki widget mevcut `widgetContext.executeIonApiAsync` çağrısını kullanmaya
devam edebilsin — böylece tarayıcı tarafında CORS ve token derdi olmaz,
kullanıcı kimliği Infor oturumundan gelir.

```
Widget ──executeIonApiAsync("TAKVIMAPI/v1/m3/exec")──▶ ION API Gateway
                                                          │  (X-Api-Key ekler)
                                                          ▼
                                              Heroku: Buying Takvim M3 API
                                                          │  (servis hesabı)
                                                          ▼
                                                    M3 CUSEXTMI
```

## Adımlar (Infor OS → ION API)

1. **ION API → Available APIs → (+) Add** → *Import from file* ile
   `ion/swagger-2.0.json` dosyasını yükle. Dosyada Heroku adresi zaten yazılı:
   `buyingcalender-1ff62b7c0e92.herokuapp.com`
2. **API name** olarak kısa bir ad ver — widget'ta çağrı yolu bu adla başlayacak.
   Öneri: `TAKVIMAPI`. Widget'taki çağrı `TAKVIMAPI/v1/m3/exec` olur.
3. **Target endpoint / Backend URL**: `https://buyingcalender-1ff62b7c0e92.herokuapp.com`
4. **Backend authentication**: sabit header ekle →
   `X-Api-Key: <Heroku Config Vars'taki API_KEY değeri>`

   Değeri Heroku → Settings → Reveal Config Vars ekranından **kopyala**;
   varsayılan bir değer yok, orada ne yazıyorsa o. Bu iki yerin birbirini
   tutması şart — tutmazsa gateway üzerinden gelen her istek 401 döner.
   Bu header, Heroku URL'inin dışarıdan doğrudan çağrılmasını engelleyen
   tek şeydir; atlanırsa API internete açık kalır.
5. **Authorized Apps** → widget'ların kullandığı uygulamaya bu API'ye erişim izni ver.
6. Kaydet ve ION API içindeki test aracıyla `GET /healthz` çağır.

## Doğrulama

```
GET  TAKVIMAPI/healthz          → {"status":"ok"}
GET  TAKVIMAPI/readyz           → {"status":"ready","ion":"ok","api_key_set":true}
POST TAKVIMAPI/v1/m3/exec       → {"MIRecord":[...]}
     {"transaction":"LstFieldValue","maxrecs":300,
      "params":{"FILE":"CPSTAKVIM","PK01":"11","PK02":"PROD"}}
```

`/healthz` doğrudan Heroku URL'inden **anahtarsız** çağrılabilir (bilerek);
`/v1/*` uçları anahtarsız çağrıldığında 401 dönmelidir. Kurulum sonrası bunu
tarayıcıdan test et — 401 gelmiyorsa `API_KEY` tanımlanmamış demektir.

## Widget tarafında karşılığı — tek satır

En düşük riskli yol `/v1/m3/x` ucu: M3'ün URL şemasını birebir taklit eder
(GET + query string + `;maxrecs=` matrix parametresi). Widget'ta değişen tek şey:

```js
// eski
M3_EXEC = "M3/m3api-rest/execute/CUSEXTMI";
// yeni
M3_EXEC = "CustomerApi/TAKVIMAPI/v1/m3/x";
```

> **`CustomerApi/` öneki şart.** ION kaydı Base URL'i şu şekilde üretiyor:
> `mingle-ionapi.eu1.inforcloudsuite.com/<TENANT>/CustomerApi/TAKVIMAPI`
> Doğrulanmış hâli ION → Documentation ekranında yazar; oradan kopyala.

`_qs`, `_parseM3`, metod (GET), hata yönetimi, rol kontrolleri — hepsi aynı kalır.

## Alternatif: POST /v1/m3/exec

Mevcut kod:

```js
const url = M3_EXEC + "/LstFieldValue;maxrecs=300?"
          + "FILE=" + encodeURIComponent(M3_FILE)
          + "&PK01=" + encodeURIComponent(this.seasonId)
          + "&PK02=" + encodeURIComponent(supply);
this.widgetContext.executeIonApiAsync({ method: "GET", url, cache: false,
                                        headers: { Accept: "application/json" } })
```

Yeni kod:

```js
this.widgetContext.executeIonApiAsync({
  method: "POST",
  url: "TAKVIMAPI/v1/m3/exec",
  cache: false,
  headers: { Accept: "application/json", "Content-Type": "application/json" },
  data: JSON.stringify({
    transaction: "LstFieldValue",
    maxrecs: 300,
    params: { FILE: M3_FILE, PK01: this.seasonId, PK02: supply },
  }),
})
```

Yanıt gövdesi aynı (`{MIRecord:[{NameValue:[…]}]}`) olduğu için widget'lardaki
`_parseM3(resp)` ve tüm alan eşleme mantığı **değişmeden** çalışır.
Rol/yetki kontrolleri de widget'ta olduğu gibi kalır — API tarafında ikinci bir
kontrol yok (`ENFORCE_ROLE_POLICY=0`).
