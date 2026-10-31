# Reverse Engineer & Rebuild

Bir URL veya uygulama dosyasından **çalışan bir yeniden yapım** üretmek için agent skill’i. Hedefin görülebilen ekranlarını ve davranışlarını [REA](https://github.com/morluto/rea) ile kaydeder, aynı akışları kodlar, özgün hedefle karşılaştırır ve saptadığı farkları giderir.

> **English:** Give your coding agent a website URL or shipped app artifact. The skill traces observable behavior, builds a runnable reconstruction in your workspace, and verifies the requested flows against the original. It reports exactly which states were compared and what remains unknown.

## Kurulum ve tek çağrı

Python 3.10+, [REA'nın desteklediği Node.js sürümü](https://github.com/morluto/rea/blob/main/docs/installation.md) (`^22.23.4 || ^24.22.1 || >=26.12.1`) ve web hedefleri için Chrome tabanlı bir tarayıcı gerekir.

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

**Tek çağrı agent iş akışını başlatır.** `skills add` skill talimatlarını kurar; REA'yı, tarayıcıyı veya Ghidra/JADX gibi analiz araçlarını kurmaz. Skill tek başına bağımsız bir `clone` terminal komutu değildir. Sonuç, hedefin erişilebilirliğine, mevcut araçlara ve incelenebilen davranışa bağlıdır.

## Skill ne yapar?

| Aşama | Somut çıktı |
| --- | --- |
| Hedefi gözlemle | REA'nın ham Evidence JSON'u, hedef kimliği, ekran/işlev akışları ve bilinmeyenler |
| Çalışan kopyayı kur | Mevcut projede açılabilen/çalıştırılabilen kod; gezinme, etkileşim, durum ve responsive görünüm |
| Hedefle karşılaştır | Aynı akışların hedef ve kopya kayıtları; ekran görüntüsü, DOM, erişilebilirlik, URL veya işlem çıktısı farkları |
| Farkları gider | Yeniden çalıştırılmış kontroller; hangi akışların eşleştiğini, hangilerinin farklı veya bilinmeyen kaldığını gösteren sonuç |

Web sitesi ve web uygulaması, bu sürümün en ayrıntılı doğrulama yoludur. APK, Electron/JavaScript, .NET, native ikili ve arşivler için de analiz ve yeniden yapım yolu vardır; bu hedeflerde gerçek davranış eşitliği ancak özgün uygulama çalıştırılabildiğinde ve karşılaştırılabilir örnekler gözlemlendiğinde söylenir. Statik analiz tek başına “birebir” kanıtı değildir.

## “Birebir” ne demek?

Skill, kullanıcının istediği ve gözlemleyebildiği **rota, ekran, giriş, etkileşim, görünüm ve çalışma koşullarında** hedefe olabildiğince yakın bir kopya amaçlar. Nihai rapor test edilen viewport'ları ve akışları açıkça listeler. Gizli sunucu mantığı, erişilemeyen hesap durumları veya hiç gözlemlenmemiş ekranlar için tam eşitlik iddiasında bulunmaz. REA'nın [web karşılaştırması](https://github.com/morluto/rea/blob/main/docs/browser-scenario-contract.md#scenario-comparison) farklı alan adlarında eşleştirmeyi reddedebilir; bu durumda kayıtlar doğrudan karşılaştırılır ve otomatik hüküm uydurulmaz.

## İlk inceleme yardımcısı

Skill içindeki Python dosyası yalnızca **ilk kanıt toplama aşamasını** terminalden çalıştırır:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/rea_once.py /absolute/path/to/target --output ./rea-report
```

Web hedefi için de URL verilebilir. Yardımcı, kurulu `rea` komutunu kullanır; yoksa `npx -y rea-agents@6.8.0` çağırır. Çıktı klasörüne `manifest.json`, `report.md`, adım adım ham JSON ve stderr kayıtları yazar; mevcut klasörün üzerine yazmaz. Çalışan kopyayı yapan, bu kanıttan sonra kod yazan ve karşılaştırmayı yürüten agent’tır.

İki web Evidence kaydını REA ile karşılaştırmak için ikinci yardımcı, Evidence katmanını otomatik açar ve sonuç ile özet tabloyu saklar:

```bash
python3 skills/reverse-engineer-and-rebuild/scripts/compare_web.py reference-evidence.json rebuild-evidence.json --output ./comparison
```

Native kodun derin analizi önceden yapılandırılmış Ghidra/Hopper/IDA/Kuna gerektirir. APK kodu için JADX ve tam JDK gerekir. Linux'taki JavaScript incelemesi için `ps` gerekir. Eksik araç halinde yardımcı mevcut kanıtı korur ve sınırı belirtir. Detaylar [skill talimatlarında](skills/reverse-engineer-and-rebuild/SKILL.md) ve [hedef rehberinde](skills/reverse-engineer-and-rebuild/references/targets.md).

## Geliştirme

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py skills/reverse-engineer-and-rebuild
```

Önceki `rea-one-shot` adı, bu sürümde `reverse-engineer-and-rebuild` olarak değişti. Mevcut kurulumları yeni adla güncelleyin.

MIT lisansı. Bu proje REA'dan bağımsızdır; REA'nın resmî dağıtımı değildir.
