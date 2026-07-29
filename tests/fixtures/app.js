class ApiClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
  }
  async fetchData(endpoint) {
    const response = await fetch(`${this.baseUrl}/${endpoint}`);
    return response.json();
  }
}

function formatResponse(data) {
  return JSON.stringify(data, null, 2);
}

const client = new ApiClient("https://api.example.com");
