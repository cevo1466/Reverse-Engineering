# Reverse Engineer & Rebuild

Bir URL veya uygulama dosyasından **çalışan bir yeniden yapım** üretmek için agent skill’i. Hedefin görülebilen ekranlarını ve davranışlarını kaydeder, aynı akışları kodlar, özgün hedefle karşılaştırır ve saptadığı farkları giderir. [REA](https://github.com/morluto/rea) varsa daha derin kanıt toplar; karşılaştırma yardımcıları yalnızca Python standart kütüphanesini kullanır.

> **English:** Give your coding agent a website URL or shipped app artifact. The skill traces observable behavior, builds a runnable reconstruction in your workspace, and verifies the requested flows against the original. It reports exactly which states were compared and what remains unknown.

[Read the English guide](README.en.md).

[skills.sh sayfasını aç](https://www.skills.sh/cevo1466/tersine-m-hendislik/reverse-engineer-and-rebuild) · [Skill talimatlarını oku](skills/reverse-engineer-and-rebuild/SKILL.md)

## Kurulum ve tek çağrı

Yardımcılar için Python 3.10+ yeterlidir. REA kullanacaksanız [desteklenen Node.js sürümü](https://github.com/morluto/rea/blob/main/docs/installation.md) (`^22.23.4 || ^24.22.1 || >=26.12.1`) gerekir. Web görünümünü gerçekten karşılaştırmak için ekran görüntüsü alabilen bir tarayıcı gerekir.

```bash
npx skills add cevo1466/Tersine-M-hendislik --skill reverse-engineer-and-rebuild
```

Ardından skill destekleyen kodlama agent’ınıza tek bir istek verin:

```text
$reverse-engineer-and-rebuild https://example.com: Bu sitenin ana sayfasını ve arama akışını mevcut projemde çalışan şekilde birebir yeniden oluştur. Masaüstü ve mobil görünümü hedefle karşılaştır; farkları gider.
```

Yerel uygulama örneği:

```text
$reverse-engineer-and-rebuild /absolute/path/to/app.asar: Ayarlar ekranını ve kaydetme davranışını çalışan bir uygulama olarak yeniden oluştur; gözlemlediğin durumlarla doğrula.
```

**Tek çağrı agent iş akışını başlatır.** `skills add` skill talimatlarını kurar; REA'yı, tarayıcıyı veya Ghidra/JADX gibi analiz araçlarını kurmaz. REA yoksa skill mevcut tarayıcı/HTTP/işletim sistemi araçlarıyla devam eder ve kanıt sınırını belirtir. Skill tek başına bağımsız bir `clone` terminal komutu değildir. Sonuç, hedefin erişilebilirliğine ve incelenebilen davranışa bağlıdır.

## Skill ne yapar?

| Aşama | Somut çıktı |
| --- | --- |
| Hedefi gözlemle | REA Evidence veya mevcut araçların kayıtları; hedef kimliği, ekran/işlev akışları ve bilinmeyenler |
| Çalışan kopyayı kur | Mevcut projede açılabilen/çalıştırılabilen kod; gezinme, etkileşim, durum ve responsive görünüm |
| Hedefle karşılaştır | Aynı akışların hedef ve kopya kayıtları; otomatik piksel farkı, DOM, erişilebilirlik, URL, HTTP yanıtı ve kalıcı durum farkları |
| Farkları gider | Yeniden çalıştırılmış kontroller; hangi akışların eşleştiğini, hangilerinin farklı veya bilinmeyen kaldığını gösteren sonuç |

Web sitesi ve web uygulaması, bu sürümün en ayrıntılı doğrulama yoludur. APK, Electron/JavaScript, .NET, native ikili ve arşivler için de analiz ve yeniden yapım yolu vardır; bu hedeflerde gerçek davranış eşitliği ancak özgün uygulama çalıştırılabildiğinde ve karşılaştırılabilir örnekler gözlemlendiğinde söylenir. Statik analiz tek başına “birebir” kanıtı değildir.

### Görsel doğrulama

`compare_web.py`, REA tarayıcı kayıtlarındaki aynı adımları otomatik eşler ve ekran görüntülerini piksel düzeyinde karşılaştırır. REA yoksa kurulu Chrome/Chromium ile `capture_web.py` aynı etkileşim senaryosunu hedefte ve kopyada çalıştırıp her adım için PNG, DOM, erişilebilirlik ağacı ve URL kaydeder. `compare_capture.py` tüm adımların görüntülerini ve rota yollarını tek çağrıda karşılaştırır:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/capture_web.py scenario.json --url http://127.0.0.1:8080/ --output ./reference-web
python3 skills/reverse-engineer-and-rebuild/scripts/capture_web.py scenario.json --url http://127.0.0.1:8081/ --output ./rebuild-web
python3 skills/reverse-engineer-and-rebuild/scripts/compare_capture.py ./reference-web/manifest.json ./rebuild-web/manifest.json --output ./web-check
```

Senaryo örneği ve uzak test hedefindeki etkileşim izni için [web rehberine](skills/reverse-engineer-and-rebuild/references/web.md) bakın. Ayrı iki PNG dosyası da `pixel_diff.py` ile karşılaştırılabilir:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/pixel_diff.py reference.png rebuild.png --output ./pixel-check
```

Araçlar değişen piksel sayısını, oranını, fark alanını ve ortalama kanal farkını JSON'a yazar; fark görseli üretir. Varsayılan ölçüt tam piksel eşitliğidir. İstenirse açıkça belirtilen kanal ve değişen piksel oranı eşikleri kullanılabilir. Aynı viewport, ölçek, tarayıcı, font ve sayfa durumu sağlanmadan piksel sonucu “birebir” kanıtı sayılmaz. Araç 8 bit, interlace olmayan RGB/RGBA PNG dosyalarını destekler.

### Backend doğrulama

Skill, istek/yanıtla birlikte **işlem sonrası durumu** kaydeder: örneğin oluşturma, ardından okuma; geçersiz giriş, ardından durumun değişmediğini okuma; güncelleme ve silme. Kaydedilmiş hedef ve kopya vakaları, ağa yeniden istek göndermeyen yardımcıyla karşılaştırılır:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/capture_http.py scenario.json --base-url http://127.0.0.1:8080 --output ./reference-api
python3 skills/reverse-engineer-and-rebuild/scripts/capture_http.py scenario.json --base-url http://127.0.0.1:8081 --output ./rebuild-api
python3 skills/reverse-engineer-and-rebuild/scripts/compare_http.py ./reference-api/transcript.json ./rebuild-api/transcript.json --output ./api-check
```

Senaryo kaydedici, ilk yanıttan üretilen kimliği sonraki isteklerde kullanabilir ve işlem sonrası durumu ayrıca okuyabilir. Uzak test sunucuları açık `--allow-remote-test` seçeneği gerektirir; yazma istekleri otomatik tekrarlanmaz. Bu yöntem test edilen iş kurallarını ve kalıcılığı doğrular. Gözlenemeyen yetkilendirme, eşzamanlılık veya gizli sunucu kurallarını kendiliğinden keşfetmez. [Backend rehberi](skills/reverse-engineer-and-rebuild/references/backend.md) vaka biçimini ve kapsama sınırını açıklar.

## “Birebir” ne demek?

Skill, kullanıcının istediği ve gözlemleyebildiği **rota, ekran, giriş, etkileşim, görünüm ve çalışma koşullarında** hedefe olabildiğince yakın bir kopya amaçlar. Nihai rapor test edilen viewport'ları ve akışları açıkça listeler. Gizli sunucu mantığı, erişilemeyen hesap durumları veya hiç gözlemlenmemiş ekranlar için tam eşitlik iddiasında bulunmaz. REA'nın [web karşılaştırması](https://github.com/morluto/rea/blob/main/docs/browser-scenario-contract.md#scenario-comparison) farklı alan adlarında eşleştirmeyi reddedebilir; bu durumda kayıtlar doğrudan karşılaştırılır ve otomatik hüküm uydurulmaz.

## İlk inceleme yardımcısı

Skill içindeki Python dosyası yalnızca **ilk kanıt toplama aşamasını** terminalden çalıştırır:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/rea_once.py /absolute/path/to/target --output ./rea-report
```

Web hedefi için de URL verilebilir. Yardımcı, kurulu `rea` komutunu kullanır; yoksa `npx -y rea-agents@6.8.0` çağırır. Çıktı klasörüne `manifest.json`, `report.md`, adım adım ham JSON ve stderr kayıtları yazar; mevcut klasörün üzerine yazmaz. Çalışan kopyayı yapan, bu kanıttan sonra kod yazan ve karşılaştırmayı yürüten agent’tır.

Her REA komutu varsayılan olarak en fazla 600 saniye çalışır; `--timeout-seconds` ile 1–3600 saniye arasında değiştirilebilir. Sıfır çıkış kodu tek başına başarı sayılmaz: JSON, beklenen REA işlemi ve `normalized_result` doğrulanır. Zaman aşımı ve geçersiz çıktı raporda başarısızlık olarak görünür.

İki web Evidence kaydını karşılaştırmak için ikinci yardımcı, Evidence katmanını otomatik açar; REA varsa onun karşılaştırmasını da çalıştırır ve piksel farklarını her adım için saklar:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/compare_web.py reference-evidence.json rebuild-evidence.json --output ./comparison
```

Tüm yardımcılar, adı ne olursa olsun yeni çıktı klasörünün içine `*` kuralı taşıyan yerel bir `.gitignore` koyar. Böylece ham DOM, istek gövdeleri ve ekran görüntüleri yanlışlıkla `git add` ile repoya girmez. Kanıtları paylaşmadan önce yine de içeriklerini gözden geçirin.

REA kullanılırsa native kodun derin analizi önceden yapılandırılmış Ghidra/Hopper/IDA/Kuna gerektirir. APK kodu için JADX ve tam JDK gerekir. Linux'taki JavaScript incelemesi için `ps` gerekir. Eksik araç halinde skill mevcut kanıtı korur ve sınırı belirtir. Detaylar [skill talimatlarında](skills/reverse-engineer-and-rebuild/SKILL.md), [araçsız gözlem rehberinde](skills/reverse-engineer-and-rebuild/references/fallback.md) ve [hedef rehberinde](skills/reverse-engineer-and-rebuild/references/targets.md).

## Geliştirme

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py skills/reverse-engineer-and-rebuild
```

Önceki `rea-one-shot` adı, bu sürümde `reverse-engineer-and-rebuild` olarak değişti. Mevcut kurulumları yeni adla güncelleyin.

MIT lisansı. Bu proje REA'dan bağımsızdır; REA'nın resmî dağıtımı değildir.
