// Static autocomplete source for the location tag-input. Deliberately just a
// plain array (no geocoding API/dependency) — the search pipeline only needs
// a location *string* to plug into search queries, not validated coordinates.
window.LOCATION_SUGGESTIONS = [
  "Remote",
  // Major tech hubs
  "San Francisco", "New York", "Seattle", "Austin", "Boston", "Los Angeles",
  "Chicago", "Denver", "Toronto", "Vancouver", "Montreal",
  "London", "Berlin", "Munich", "Amsterdam", "Paris", "Dublin", "Zurich",
  "Stockholm", "Copenhagen", "Helsinki", "Barcelona", "Madrid", "Lisbon",
  "Warsaw", "Prague",
  "Singapore", "Hong Kong", "Tokyo", "Seoul", "Shanghai", "Beijing",
  "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune",
  "Sydney", "Melbourne", "Auckland",
  "Tel Aviv", "Dubai",
  "Sao Paulo", "Mexico City",
  // Countries
  "United States", "Canada", "United Kingdom", "Ireland", "Germany",
  "France", "Netherlands", "Belgium", "Switzerland", "Austria", "Sweden",
  "Norway", "Denmark", "Finland", "Iceland", "Spain", "Portugal", "Italy",
  "Poland", "Czech Republic", "Hungary", "Romania", "Greece", "Turkey",
  "Japan", "South Korea", "China", "India", "Indonesia",
  "Malaysia", "Thailand", "Vietnam", "Philippines", "Taiwan",
  "Australia", "New Zealand", "Israel", "United Arab Emirates",
  "Saudi Arabia", "South Africa", "Nigeria", "Kenya", "Egypt",
  "Brazil", "Mexico", "Argentina", "Chile", "Colombia",
];
