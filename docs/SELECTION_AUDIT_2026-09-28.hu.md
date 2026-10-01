# Ajánlási és tétezési audit — 2026-09-28

A felhasználó nem adott meg 10 USD napi összegkeretet. Ezt az alapbeállítást eltávolítjuk: `daily_risk_limit=null`. A fogadásonkénti maximum továbbra is 5 USD. A felső limit csak új felhasználói utasítás alapján változhat, nem automatikusan néhány nyertes fogadás után. Bankroll nélkül az alapegység 1 USD; ebből nem állítható személyre szabott optimális Kelly-tét.

A napi cél legalább 15 különálló, téttel kiadott ajánlás, felső darabszámkorlát nélkül. Az öt liga ellenőrzése 60 másodperces ciklusban fut, az aznapi és a következő 48 órás kezdésekre. Az árforrás cache TTL-je 60 másodperc; ez nem garantált másodpercenkénti adatfolyam. A web a publikációról 3 másodperces eseményellenőrzéssel értesül. Kezdés után az előmeccses ajánlás nem használható élő fogadásként.

## Kiválasztás és mérés

A sportmodell odds nélküli becslést készít. Ezután kizárólag az egyező Stake-esemény aktív, pontos piacára, vonalára és oddsára számítunk értéket. A rangsor elsődlegesen a stresszelt becsült megtérülést használja, nem a nyers találati valószínűséget. A 3 százalékpontos minimum stresszelt ROI és a legfeljebb két piaccsalád/meccs korlát megmarad. Az egyszerű stresszeltetés nem bizonyított konfidenciaintervallum vagy kalibrált bizonytalansági modell.

A `recommendation_issuance` az első téttel kiadott ajánlást tárolja meccs/piaccsalád szerint, eredeti kiadási dátummal. Budapest helyi naptári napjai szerint számol, a téli/nyári időszámítás napjain is. Új odds, alternatív vonal, dupla szelvény, nulla tétű Watchlist-bejegyzés nem növeli a napi számlálót. A cél elérése után az új megfelelő ajánlások kiadása folytatódik; hiány esetén a tényleges hiány jelenik meg.

Az importált 17 szelvény 16 különálló választást tartalmaz. Ebből az eredeti modellpillanatképek alapján 9 kapott pozitív ajánlott tétet; 7 választás akkor nulla tétű jelölt volt. A felhasználó ezekre is fogadott, ezért mind a 17 valódi, bejelentett szelvény marad a pénzügyi naplóban, de nem állítjuk, hogy 17 megfelelő, téttel kiadott ajánlás keletkezett. A régi kiadásszámláló rekonstruálása az eredeti pozitív tétből és időbélyegből történik, új fogadás létrehozása nélkül.

## Jelenleg telepített modellek

A telepített `data/models/{liga}.joblib` objektumok jelentéseit ellenőriztük. A `summary.json` újabb, még nem előléptetett modelljelölteket is leírhat; ezért önmagában nem azonos az éles modellek riportjával. A Brier kisebb értéke kedvezőbb.

| Liga | Tesztmeccs | Modell Brier | Elo Brier |
| --- | ---: | ---: | ---: |
| NBA | 720 | 0.200314 | 0.197402 |
| MLB | 1598 | 0.246768 | 0.251088 |
| NHL | 768 | 0.245740 | 0.246413 |
| NFL | 239 | 0.227614 | 0.222925 |
| WNBA | 199 | 0.202964 | 0.197675 |

Ez nem igazol általános modellfölényt: három ligában az Elo jobb ezen a mintán. Végrehajtható történeti árakkal végzett profit-visszateszt még nincs, teljes sérülés- és kezdőcsapat-hatás nincs betanítva. Az eredményeket és a díjak utáni profitot előre rögzített ajánlásokon kell mérni, ligánként és piaconként is. A mostani konfiguráció- és rangsorváltozás nem pótolja ezeket a fejlesztéseket.

Kapcsolódó tudáskártyák: K01 várható érték; K08 időrendi validáció; K09 bankroll és Kelly. Ezek indokolják az érték szerinti sorrendet, az adat- és árillesztés ellenőrzését, valamint a tétezés és a bizonyított profit megkülönböztetését. A profit a rendszer célja; a napi darabszám kikényszerítése nem bizonyítja, hogy azt eléri.
