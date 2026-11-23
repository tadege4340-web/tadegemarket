
document.addEventListener("DOMContentLoaded", function () {
    console.log("ShopNova JavaScript loaded successfully.");

    // Mobile navigation toggle
    const menuButton = document.querySelector("[data-menu-toggle]");
    const navigation = document.querySelector("[data-menu]");

    if (menuButton && navigation) {
        menuButton.addEventListener("click", function () {
            navigation.classList.toggle("active");
        });
    }

    // Close mobile navigation when a link is selected
    if (navigation) {
        navigation.querySelectorAll("a").forEach(function (link) {
            link.addEventListener("click", function () {
                navigation.classList.remove("active");
            });
        });
    }
});
