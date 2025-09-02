# core/widgets.py
from django.forms.widgets import FileInput
from django.utils.safestring import mark_safe

class AvatarInput(FileInput):
    """Простой <input type=file>: без 'Currently' и 'Clear', с автопревью и автосабмитом."""
    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs or {}
        input_id = attrs.get("id", f"id_{name}")
        attrs.update({
            "accept": "image/*",
            "id": input_id,
            "class": (attrs.get("class", "") + " avatar-input").strip(),
        })
        html = super().render(name, value, attrs, renderer)
        js = f"""
<script>
(function(){{
  var input = document.getElementById("{input_id}");
  if(!input) return;
  input.addEventListener("change", function(){{
    var f = this.files && this.files[0];
    if(f){{
      var url = URL.createObjectURL(f);
      document.querySelectorAll(".js-avatar-preview").forEach(function(el){{
        if(el.tagName.toLowerCase()==="img") el.src = url;
        else {{ el.style.backgroundImage = "url("+url+")"; el.textContent=""; }}
      }});
      if (this.form) this.form.requestSubmit(); // автосохранение
    }}
  }});
}})();
</script>
"""
        return mark_safe(html + js)
