// Открой страницу услуг, прокрути вниз чтобы всё подгрузилось, потом запусти:
let links = document.querySelectorAll('a[href*="/market/product/"]');
let ids = new Set();
links.forEach(a => {
    let match = a.href.match(/market\/product\/[^/]+-(\d+)(?:\?|$)/);
    if (match) ids.add(match[1]);
});
console.log("Найдено услуг:", ids.size);
console.log("ID:", [...ids].join(","));

