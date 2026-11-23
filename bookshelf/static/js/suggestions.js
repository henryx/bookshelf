// A field's suggestion list (an ARIA combobox and listbox) and picking from it.
(function () {
  "use strict";

  // Suggestions: a [data-suggest] field (role="combobox": a book's title or
  // ISBN, an author's name or surname, a category, the publisher) asks the
  // server for matches as you type, sending its text as q instead of under
  // its own form name (e.g. authors-0-name).
  document.addEventListener("htmx:configRequest", function (event) {
    var input = event.detail.elt;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-suggest]")) {
      return;
    }
    delete event.detail.parameters[input.name];
    event.detail.parameters.q = input.value;
  });

  // The matches come back as the options of the field's listbox (its
  // aria-controls), shown under it while it has the focus. Up and Down move
  // through them, Enter or a click picks one, Escape or leaving the field
  // closes the list. Our own list, not a <datalist>: Firefox opens a
  // datalist's dropdown only for the options it holds when a key is
  // pressed, not for those the server sends a moment later.
  function suggestionList(input) {
    return document.getElementById(input.getAttribute("aria-controls"));
  }

  function suggestionField(list) {
    return document.querySelector('[aria-controls="' + list.id + '"]');
  }

  function suggestions(list) {
    return Array.prototype.slice.call(list.querySelectorAll('[role="option"]'));
  }

  function setActive(input, option) {
    suggestions(suggestionList(input)).forEach(function (each) {
      each.setAttribute("aria-selected", String(each === option));
    });
    if (option) {
      input.setAttribute("aria-activedescendant", option.id);
      option.scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function showSuggestions(input, shown) {
    var list = suggestionList(input);
    if (!list) {
      return;
    }
    list.hidden = !shown;
    input.setAttribute("aria-expanded", String(shown));
    if (!shown) {
      setActive(input, null);
    }
  }

  function moveSuggestion(input, step) {
    var list = suggestionList(input);
    var options = suggestions(list);
    if (!options.length) {
      return;
    }
    showSuggestions(input, true);
    var index = options.indexOf(list.querySelector('[aria-selected="true"]'));
    var next = index < 0 ? (step > 0 ? 0 : options.length - 1)
      : (index + step + options.length) % options.length;
    setActive(input, options[next]);
  }

  // What picking an option does besides filling the field: an author fills
  // both fields of the row; a catalogue book ([data-book-pick]) is loaded
  // into the form, read-only, to add it to the collection (the form's
  // [data-book-loader] fetches it).
  function pickSuggestion(input, option) {
    input.value = option.dataset.value;
    showSuggestions(input, false);
    if (input.matches("[data-author-field]")) {
      var row = input.closest("[data-row]");
      row.querySelector('[data-author-field="name"]').value = option.dataset.name;
      row.querySelector('[data-author-field="surname"]').value = option.dataset.surname;
    }
    var loader = input.form && input.form.querySelector("[data-book-loader]");
    if (input.matches("[data-book-pick]") && option.dataset.bookId && loader) {
      loader.setAttribute("hx-vals", JSON.stringify({ book: option.dataset.bookId }));
      htmx.trigger(loader, "book-picked");
    }
  }

  document.addEventListener("htmx:afterSwap", function (event) {
    var list = event.detail.target;
    if (!list.matches('[role="listbox"]')) {
      return;
    }
    var input = suggestionField(list);
    if (!input) {
      return;
    }
    var options = suggestions(list);
    options.forEach(function (option, index) {
      option.id = list.id + "-" + index;
      option.setAttribute("aria-selected", "false");
    });
    showSuggestions(input, options.length > 0 && document.activeElement === input);
  });

  document.addEventListener("keydown", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-suggest]")) {
      return;
    }
    var list = suggestionList(input);
    var open = list && !list.hidden;
    var active = open && list.querySelector('[aria-selected="true"]');
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveSuggestion(input, event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter" && active) {
      // Picks the option instead of submitting the form.
      event.preventDefault();
      pickSuggestion(input, active);
    } else if (event.key === "Escape" && open) {
      // Closes the list, not the dialog the field is in.
      event.preventDefault();
      showSuggestions(input, false);
    }
  });

  // Typing changes the text the options were for: none is chosen any more.
  document.addEventListener("input", function (event) {
    var input = event.target;
    if (input instanceof HTMLInputElement && input.matches("[data-suggest]") && suggestionList(input)) {
      setActive(input, null);
    }
  });

  document.addEventListener("focusout", function (event) {
    var input = event.target;
    if (input instanceof HTMLInputElement && input.matches("[data-suggest]")) {
      showSuggestions(input, false);
    }
  });

  // Pressing an option would take the focus from the field, closing the list.
  document.addEventListener("mousedown", function (event) {
    if (event.target.closest('.suggestions [role="option"]')) {
      event.preventDefault();
    }
  });

  document.addEventListener("click", function (event) {
    var option = event.target.closest('.suggestions [role="option"]');
    var input = option && suggestionField(option.closest('[role="listbox"]'));
    if (input) {
      pickSuggestion(input, option);
    }
  });
})();
