/* Invoice item rows — shared by create_invoice.html and edit_invoice.html.
   Form field names (product_id[], product_name[], quantity[], unit_price[]) are unchanged. */
(function () {
  var form = document.getElementById('invoiceForm');
  var body = document.getElementById('itemsBody');
  if (!form || !body) return;

  var productMap = {};
  try { productMap = JSON.parse(form.dataset.products || '{}'); } catch (e) {}

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function money(n) {
    return 'Rs. ' + n.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function optionsHTML() {
    return '<option value="">Select a product</option>' + Object.keys(productMap).map(function (id) {
      var p = productMap[id];
      return '<option value="' + esc(id) + '">' + esc(p.name) + ' (Rs. ' + p.price + ')</option>';
    }).join('');
  }
  function rowHTML(n) {
    return '<td class="sr-cell">' + n + '</td>' +
      '<td class="cell-product" data-label="Product">' +
        '<input type="hidden" name="product_id[]" class="pid" value="">' +
        '<select class="prod-select" aria-label="Select product">' + optionsHTML() + '</select>' +
        '<input type="text" name="product_name[]" class="pname" placeholder="Or type a name" required>' +
      '</td>' +
      '<td data-label="Qty"><input type="number" name="quantity[]" class="qty" value="1" min="1" required></td>' +
      '<td data-label="Unit price (Rs.)"><input type="number" name="unit_price[]" class="price" step="0.01" min="0" placeholder="0.00" required></td>' +
      '<td class="cell-amount" data-label="Amount (Rs.)"><input type="text" class="amount" readonly tabindex="-1" placeholder="0.00"></td>' +
      '<td class="cell-remove"><button type="button" class="remove-btn" aria-label="Remove row"><svg class="i"><use href="#i-x"/></svg></button></td>';
  }

  function calcRow(tr) {
    var q = parseFloat(tr.querySelector('.qty').value) || 0;
    var p = parseFloat(tr.querySelector('.price').value) || 0;
    tr.querySelector('.amount').value = (q * p).toFixed(2);
  }
  function calcTotal() {
    var total = 0, rows = body.querySelectorAll('tr');
    rows.forEach(function (tr) { total += parseFloat(tr.querySelector('.amount').value) || 0; });
    var t = document.getElementById('grandTotal'); if (t) t.textContent = money(total);
    var c = document.getElementById('itemCount'); if (c) c.textContent = rows.length;
  }
  function renumber() {
    body.querySelectorAll('tr').forEach(function (tr, i) { tr.querySelector('.sr-cell').textContent = i + 1; });
  }

  body.addEventListener('input', function (e) {
    if (e.target.matches('.qty, .price')) { calcRow(e.target.closest('tr')); calcTotal(); }
  });
  body.addEventListener('change', function (e) {
    if (!e.target.matches('.prod-select')) return;
    var tr = e.target.closest('tr'), id = e.target.value;
    if (id && productMap[id]) {
      tr.querySelector('.pname').value = productMap[id].name;
      tr.querySelector('.price').value = Number(productMap[id].price).toFixed(2);
      tr.querySelector('.pid').value = id;
    } else {
      tr.querySelector('.pid').value = '';
    }
    calcRow(tr); calcTotal();
  });
  body.addEventListener('click', function (e) {
    var btn = e.target.closest('.remove-btn');
    if (!btn || body.querySelectorAll('tr').length <= 1) return;
    btn.closest('tr').remove(); renumber(); calcTotal();
  });

  var add = document.getElementById('addRowBtn');
  if (add) add.addEventListener('click', function () {
    var tr = document.createElement('tr');
    tr.className = 'item-row';
    tr.innerHTML = rowHTML(body.querySelectorAll('tr').length + 1);
    body.appendChild(tr);
    tr.querySelector('.pname').focus();
  });

  var d = document.getElementById('invoiceDateDisplay');
  if (d) d.textContent = new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

  body.querySelectorAll('tr').forEach(calcRow);
  calcTotal();
})();
