# ION API Gateway'e custom API olarak tanıtma

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
   `ion/swagger-2.0.json` dosyasını yükle.
   - Yüklemeden önce dosyadaki `"host": "DEGISTIR.herokuapp.com"` satırını
     gerçek Heroku adresiyle değiştir.
2. **API name** olarak kısa bir ad ver — widget'ta çağrı yolu bu adla başlayacak.
   Öneri: `TAKVIMAPI`. Widget'taki çağrı `TAKVIMAPI/v1/m3/exec` olur.
3. **Target endpoint / Backend URL**: `https://<app>.herokuapp.com`
4. **Backend authentication**: sabit header ekle →
   `X-Api-Key: <Heroku'daki API_KEY değeri>`
   Bu, Heroku URL'inin dışarıdan doğrudan çağrılmasını engelleyen tek şeydir;
   atlanırsa API internete açık kalır.
5. **Authorized Apps** → widget'ların kullandığı uygulamaya bu API'ye erişim izni ver.
6. Kaydet ve ION API içindeki test aracıyla `GET /healthz` çağır.

## Doğrulama

```
GET  TAKVIMAPI/healthz          → {"status":"ok"}
GET  TAKVIMAPI/readyz           → {"status":"ready","ion":"ok"}
POST TAKVIMAPI/v1/m3/exec       → {"MIRecord":[...]}
     {"transaction":"LstFieldValue","maxrecs":300,
      "params":{"FILE":"CPSTAKVIM","PK01":"11","PK02":"PROD"}}
```

`/healthz` doğrudan Heroku URL'inden **anahtarsız** çağrılabilir (bilerek);
`/v1/*` uçları anahtarsız çağrıldığında 401 dönmelidir. Kurulum sonrası bunu
tarayıcıdan test et — 401 gelmiyorsa `API_KEY` tanımlanmamış demektir.

## Widget tarafında karşılığı

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
